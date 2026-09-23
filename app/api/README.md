# HTTP API

# HTTP API

FastAPI entry point: `app/api/main.py` (`uvicorn app.api.main:app`). It exposes:

- `GET /api/health` — service readiness.
- `POST /api/meetings/process` — process an uploaded audio/video recording after consent.
- `POST /api/meetings/export` — export edited meeting results as DOCX or PDF.

The browser client uses the same-origin `/api` path: Vite proxies it during development and Nginx proxies it in Docker Compose. Processing uses the existing local services in `app/services/`.
