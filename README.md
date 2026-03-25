# FastAPI Migration (HTML_DITA_Backend)

This folder contains a FastAPI implementation of the original Express API.

## Endpoints

- `GET /`
- `POST /api/convert` (single API with live streaming progress)
- `POST /api/pre-flight-check` (backward-compatible alias to `/api/convert`)
- `GET /api/download/{user_id}/{download_id}`
- `POST /api/insertDitaTag`
- `GET /api/insertDitaTag`

## Setup

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Create `.env` (example below) and update values.
4. Start the server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

## .env Example

```env
PORT=8001
BASE=http://localhost:8001
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=htmltodita
```

## Conversion Flow (Single Streaming API)

1. Call `POST /api/convert` with `multipart/form-data` (`zipFile` file; optional `userId`).
2. Read the response stream (SSE-formatted chunks).
3. Update UI stepper from each event payload.
4. On `completed` event, use `downloadLink` for file download.

## Stream Events

- Event types: `progress`, `failed`, `completed`
- Events are intentionally paced with a `2s` delay between step updates for clearer UI progression.
- Payload fields: `message`, `status`, `userId`, `jobState`, `currentStep`, `steps`, optional `failedStep`, optional `invalidFiles`, optional `downloadLink`

## Notes

- `userId` is optional and defaults to `default`.
- Output archives are created under `downloads/<download_id>/` and deleted after download.
