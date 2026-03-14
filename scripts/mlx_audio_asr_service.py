#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mlx_audio.stt import load


def _normalize_requested_language(language: str | None) -> str | None:
    normalized = str(language or "").strip()
    if not normalized or normalized.lower() == "auto":
        return None
    aliases = {
        "zh": "Chinese",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "yue": "Cantonese",
        "mandarin": "Chinese",
    }
    return aliases.get(normalized.lower(), normalized)


def _normalize_result_language(language: object, fallback: str | None = None) -> str:
    if isinstance(language, (list, tuple)):
        for item in language:
            value = str(item or "").strip()
            if value:
                return value
    value = str(language or "").strip()
    if value:
        return value
    return str(fallback or "Auto")


class _State:
    def __init__(self, *, model_id: str, aligner_id: str) -> None:
        self.model_id = model_id
        self.aligner_id = aligner_id
        self.model = load(model_id)
        self.aligner = load(aligner_id)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "MLXAudioASR/1.0"

    @property
    def state(self) -> _State:
        return self.server.state  # type: ignore[attr-defined]

    def _send_json(self, payload: dict, *, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        self._send_json(
            {
                "ok": True,
                "model_id": self.state.model_id,
                "aligner_id": self.state.aligner_id,
            }
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/transcribe":
            self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            audio_path = str(payload.get("audio_path") or "").strip()
            requested_language = _normalize_requested_language(payload.get("language"))
            if not audio_path:
                raise ValueError("Missing audio_path.")

            result = self.state.model.generate(audio_path, language=requested_language)
            recognized_text = str(getattr(result, "text", "") or "").strip()
            result_language = _normalize_result_language(
                getattr(result, "language", ""),
                fallback=requested_language,
            )
            result_segments = list(getattr(result, "segments", []) or [])

            aligned_items: list[dict[str, float | int | str]] = []
            if recognized_text:
                aligned = self.state.aligner.generate(
                    audio_path,
                    text=recognized_text,
                    language=result_language,
                )
                for index, item in enumerate(getattr(aligned, "items", []) or []):
                    aligned_items.append(
                        {
                            "index": index,
                            "text": str(getattr(item, "text", "") or "").strip(),
                            "start": round(float(getattr(item, "start_time", 0.0) or 0.0), 3),
                            "end": round(float(getattr(item, "end_time", 0.0) or 0.0), 3),
                        }
                    )
            self._send_json(
                {
                    "ok": True,
                    "text": recognized_text,
                    "language": result_language,
                    "segments": result_segments,
                    "items": aligned_items,
                }
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Local MLX Audio ASR service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50003)
    parser.add_argument("--model", required=True)
    parser.add_argument("--aligner", required=True)
    args = parser.parse_args()

    state = _State(model_id=args.model, aligner_id=args.aligner)
    httpd = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    httpd.state = state  # type: ignore[attr-defined]
    print(
        json.dumps(
            {
                "ok": True,
                "host": args.host,
                "port": args.port,
                "model_id": args.model,
                "aligner_id": args.aligner,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
