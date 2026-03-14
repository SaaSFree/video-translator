#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mlx_audio.tts.generate import generate_audio
from mlx_audio.tts.utils import load_model
from mlx_audio.utils import get_model_path


def _language_code(language: str) -> str:
    normalized = str(language or "").strip().lower()
    mapping = {
        "auto": "auto",
        "english": "en",
        "en": "en",
        "chinese": "zh",
        "mandarin": "zh",
        "zh": "zh",
        "cantonese": "yue",
        "yue": "yue",
        "japanese": "ja",
        "ja": "ja",
        "korean": "ko",
        "ko": "ko",
        "french": "fr",
        "fr": "fr",
        "german": "de",
        "de": "de",
        "italian": "it",
        "it": "it",
        "portuguese": "pt",
        "pt": "pt",
        "russian": "ru",
        "ru": "ru",
        "spanish": "es",
        "es": "es",
    }
    return mapping.get(normalized, "en")


class _State:
    def __init__(self, *, model_id: str, voice: str | None) -> None:
        self.model_id = model_id
        self.model_path = get_model_path(model_id)
        self.model = load_model(self.model_path)
        normalized_voice = str(voice or "").strip()
        self.voice = normalized_voice or None


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "MLXAudioTTS/1.0"

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
                "model_path": str(self.state.model_path),
                "voice": self.state.voice,
                "sample_rate": int(getattr(self.state.model, "sample_rate", 24000)),
            }
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/tts":
            self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            text = str(payload.get("text") or "").strip()
            ref_audio = str(payload.get("ref_audio") or "").strip()
            ref_text = str(payload.get("ref_text") or "").strip()
            lang_code = _language_code(str(payload.get("language") or "English"))
            max_tokens = int(payload.get("max_new_tokens") or 1200)
            requested_voice = payload.get("voice", self.state.voice)
            voice = None
            if requested_voice is not None:
                normalized_voice = str(requested_voice).strip()
                if normalized_voice:
                    voice = normalized_voice
            if not text:
                raise ValueError("Missing text.")
            if not ref_audio:
                raise ValueError("Missing ref_audio.")
            output_dir = Path(tempfile.mkdtemp(prefix="mlx-tts-"))
            file_prefix = "response"
            generate_audio(
                text=text,
                model=self.state.model,
                voice=voice,
                ref_audio=ref_audio,
                ref_text=ref_text or None,
                output_path=str(output_dir),
                file_prefix=file_prefix,
                audio_format="wav",
                join_audio=True,
                play=False,
                verbose=False,
                stream=False,
                max_tokens=max_tokens,
                lang_code=lang_code,
                temperature=0.0,
            )
            output_path = output_dir / f"{file_prefix}.wav"
            audio_bytes = output_path.read_bytes()
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio_bytes)))
        self.end_headers()
        self.wfile.write(audio_bytes)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Local MLX Audio TTS service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50002)
    parser.add_argument("--model", required=True)
    parser.add_argument("--voice", default="")
    args = parser.parse_args()

    state = _State(model_id=args.model, voice=args.voice)
    httpd = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    httpd.state = state  # type: ignore[attr-defined]
    print(
        json.dumps(
            {
                "ok": True,
                "host": args.host,
                "port": args.port,
                "model_id": args.model,
                "model_path": str(state.model_path),
                "voice": state.voice,
                "sample_rate": int(getattr(state.model, "sample_rate", 24000)),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
