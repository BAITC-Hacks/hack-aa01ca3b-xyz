# HTTP API

# HTTP API

FastAPI entry point: `app/api/main.py` (`uvicorn app.api.main:app`). It exposes:

- `GET /api/health` — liveness check.
- `GET /api/health/ready` — checks whether local speech models are installed; it never downloads models.
- `POST /api/meetings/process` — process an uploaded audio/video recording after consent.
- `POST /api/meetings/export` — export edited meeting results as DOCX or PDF.

Processing requires participant consent, accepts files up to 512 MiB, and runs one heavy transcription job at a time to protect local memory. Uploads are held in a temporary directory and removed when processing finishes. Export requests have bounded list and text sizes and accept only PDF/DOCX. The browser client uses the same-origin `/api` path: Vite proxies it during development and Nginx proxies it in Docker Compose. Processing uses local services in `app/services/`.
