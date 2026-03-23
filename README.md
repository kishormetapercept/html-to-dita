# FastAPI Migration (HTML_DITA_Backend)

This folder contains a FastAPI implementation of the original Express API.

## Endpoints

- `GET /`
- `POST /api/pre-flight-check`
- `POST /api/htmltodita`
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
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## .env Example

```env
PORT=8000
BASE=http://localhost:8000
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=htmltodita
```

## Notes

- `/api/pre-flight-check` expects `multipart/form-data` with:
  - `userId` (text)
  - `zipFile` (file). It also accepts `file` or `zip`.
- `/api/htmltodita` accepts JSON or form-data with `userId`.
- Output archives are created under `downloads/<download_id>/` and deleted after download.
