# Open WebUI LAN Access

This workspace includes a simple Docker-based Open WebUI entrypoint for the local `ollama` model `qwen3:8b`.

## Start

```bash
./scripts/start_open_webui_lan.sh
```

Defaults:

- container name: `open-webui-qwen3`
- image: `ghcr.io/open-webui/open-webui:main`
- LAN port: `3000`
- Ollama endpoint inside the container: `http://host.docker.internal:11434`
- persisted data directory: `tmp/open-webui-data`

The script verifies that:

- Docker is available
- Ollama is reachable at `127.0.0.1:11434`
- `qwen3:8b` exists in `ollama list`

## Stop

```bash
./scripts/stop_open_webui_lan.sh
```

## Useful overrides

Change the LAN port:

```bash
OPEN_WEBUI_PORT=3001 ./scripts/start_open_webui_lan.sh
```

Disable login for a trusted LAN only:

```bash
WEBUI_AUTH=False ./scripts/start_open_webui_lan.sh
```

## Notes

- On first launch with auth enabled, Open WebUI will ask you to create the first admin account.
- `qwen3:8b` should appear in the model picker automatically.
- If you want Qwen reasoning blocks to render more cleanly in Open WebUI, consider restarting Ollama with a reasoning parser that matches the model output format.
