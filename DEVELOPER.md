# HTML to DITA Conversion Service (FastAPI) — Developer Doc

## What this service does

This is a FastAPI backend that accepts a ZIP archive of HTML files, validates the contents, converts HTML → DITA, and returns progress updates via Server‑Sent Events (SSE). Once conversion completes it provides a download link to fetch the converted output as a ZIP.

## Repo map (high level)

```
html-to-dita-fastapi/
|-- run.py                      # Convenience entry point for uvicorn
|-- config.yml                  # YAML settings (can be overridden)
|-- README.md                   # Quickstart / API overview
`-- app/
    |-- main.py                 # FastAPI app init + middleware + router
    |-- api/routes.py           # Endpoints + SSE pipeline
    |-- core/config.py          # Settings loading (env/.env/YAML)
    |-- constants/messages.py   # Response + SSE messages
    |-- db/postgres.py          # Postgres connection + get_db()
    |-- schemas/auth.py         # TagItem schema for /api/insertDitaTag
    `-- services/files.py       # ZIP + conversion + packaging helpers
```

## Configuration (how settings are loaded)

Settings are defined in `app/core/config.py` (`Settings`) and loaded once via `get_settings()` (cached with `lru_cache`).

Source priority (highest → lowest):
1. Environment variables
2. `.env`
3. `config.yml`
4. Code defaults

Key settings (see `config.yml`):
- `port`, `base`
- `postgres_uri`
- `input_root`, `output_root`, `downloads_root`

## Step-by-step: what happens from “start” to “download”

### 1) Starting the server

You typically start one of:
- `python run.py`
  - `run.py` reads settings (`get_settings()`), then runs `uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)`.
- `uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload`

### 2) App import + initialization (`app/main.py`)

When `app.main` is imported:
1. Settings are loaded: `settings = get_settings()`.
2. `app = FastAPI(title=settings.app_name)` is created.
3. Logging is configured (`logging.basicConfig(...)`) and a module logger is created.
4. Middleware is registered:
   - CORS middleware (`allow_origins=["*"]`, all methods/headers).
   - `@app.middleware("http") log_requests` request logger.
5. An exception handler for `RequestValidationError` is added (returns a 422 JSON payload with `INVALID_REQUEST_PAYLOAD`).
6. The API router from `app/api/routes.py` is included: `app.include_router(router)`.

### 3) Per-request logging middleware (`log_requests`)

For every request:
1. It captures a start timestamp (`time.perf_counter()`).
2. It reads gateway headers into a context dict (`x-user-id`, `x-user-login`, `x-user-name`, `x-user-email`, `x-user-profile-url`).
3. It executes the actual route via `await call_next(request)`.
4. It logs a single line with method/path/query/status/duration and (if present) gateway user context.
5. If the route raised, it logs `request_failed ...` and re-raises.

### 4) Health check (`GET /`)

`app/api/routes.py:health_check` returns:
```json
{"message":"Html2Dita Backend","status":"Online"}
```

### 5) Convert endpoint (`POST /api/convert`) — the “upload” step

`app/api/routes.py:convert` does:
1. Parse multipart payload: `form = await request.form()`
   - On failure: returns a streaming SSE response with a single `failed` event (`INVALID_MULTIPART_PAYLOAD`).
2. Determine `user_id`:
   - prefers `form["userId"]`
   - else uses gateway `x-user-id`
   - else `"default"`
3. Compute the base URL for the download link:
   - If `x-forwarded-proto` + `x-forwarded-host` are set, uses them (and optional `x-forwarded-prefix`).
   - Else uses `request.base_url`.
4. Get the uploaded ZIP file:
   - prefers `zipFile`
   - else legacy fields `file` or `zip`
   - If missing: returns a single SSE `failed` event (`NO_ZIP_FILE_PROVIDED`).
5. Prepare per-user working directories:
   - `prepare_user_directories(user_id, input_root, output_root)`:
     - deletes any existing `input/<user_id>/` and `output/<user_id>/`
     - recreates them empty
6. Save and extract the ZIP:
   - `save_and_extract_zip(upload, input_dir)`:
     - validates `.zip` extension
     - saves to `input/<user_id>/<filename>.zip`
     - extracts the archive into `input/<user_id>/`
     - deletes the saved ZIP file
7. Return the SSE stream:
   - A `StreamingResponse` wraps `_pipeline_event_stream(...)` which produces the rest of the progress events.

### 6) SSE conversion pipeline (`_pipeline_event_stream`) — preflight → convert → package

`app/api/routes.py:_pipeline_event_stream` yields SSE events formatted like:

```
event: progress
data: {"message":"...","status":202,...}
```

It executes these phases (with an intentional `2s` delay between many step updates; see `STREAM_EVENT_DELAY_SECONDS`):

1. **Pre-flight check**
   - Emits `progress` for `preFlightCheck`.
   - Finds HTML files (`find_html_files(input_dir)`; scans for `*.html` and `*.htm`).
   - Validates each HTML has a `<title>` (`find_html_without_title(...)`).
   - On failure:
     - emits `failed` with `failedStep=preFlightCheck` (and `invalidFiles` when relevant)
     - deletes `input/<user_id>/` and `output/<user_id>/`
     - returns (stream ends)
   - On success:
     - emits `step_completed` for `preFlightCheck`.

2. **Transformation**
   - Emits `progress` for `transformation`.
   - Converts input files: `convert_input_to_dita(input_dir, output_dir)`:
     - for each HTML: generates DITA content (see below)
     - for each image: copies into `output/<user_id>/images/<name>`
   - Writes `output/<user_id>/index.ditamap` referencing all generated `.ditamap`/`.dita` outputs (`write_ditamap(...)`).
   - Emits `step_completed` for `transformation`.

3. **Post-flight check + packaging**
   - Emits `progress` for `postFlightCheck`.
   - Creates a fresh download id: `download_id = uuid4().hex`.
   - Creates a ZIP at: `downloads/<download_id>/<zip_name>`:
     - `zip_name` is the original upload filename if it ends with `.zip`, otherwise `<user_id>.zip`.
     - `create_zip_archive(output_dir, output_zip_path)` zips the entire `output/<user_id>/` tree.
   - Emits `step_completed` for `postFlightCheck`.

4. **Cleanup + final completion**
   - Deletes `input/<user_id>/` and `output/<user_id>/` (`remove_user_io_directories(...)`).
   - Emits `completed` with `downloadLink`:
     - `<base>/api/download/<user_id>/<download_id>`

Error handling inside the pipeline:
- `ValueError` becomes a `failed` SSE event with `status=400`.
- Any other exception becomes a `failed` SSE event with `status=500`.
- Both paths still run cleanup of `input/<user_id>/` and `output/<user_id>/`.

### 7) HTML → DITA conversion (what `convert_input_to_dita` actually generates)

Conversion logic lives in `app/services/files.py`.

For each HTML file:
1. `convert_html_file_to_dita(html_path, dita_path, output_root)` parses HTML with BeautifulSoup.
2. It picks a document title:
   - first heading (`h1`..`h6`) if present, else `<title>`, else filename stem.
3. It builds a section tree from headings and converts blocks (`p`, lists, tables, blockquotes, pre/code, images) to DITA-ish XML fragments.
4. It writes a topic folder under `output/<user_id>/` based on the input’s relative path:
   - e.g. input `guides/a.html` → `output/<user_id>/guides/a/`
5. It writes:
   - a main topic file named from the title (slugified)
   - additional section topic files (when headings produce sections)
   - a per-input `*.ditamap` inside that topic folder
6. It returns the per-input `*.ditamap` relative path so `index.ditamap` can reference it.

For images:
- `convert_input_to_dita` copies known image extensions into `output/<user_id>/images/`.

### 8) Download endpoint (`GET /api/download/{user_id}/{download_id}`)

`app/api/routes.py:download`:
1. Locates the ZIP file under `downloads/<download_id>/` (`resolve_zip_file(...)`).
2. Returns a `FileResponse` with `filename=<zipname>`.
3. Runs a background cleanup task after the response:
   - `BackgroundTask(clear_directory_contents, settings.downloads_root)`
   - This clears *the entire* `downloads_root` directory (not just the one download id).

### 9) Tag endpoints (PostgreSQL)

PostgreSQL wiring is in `app/db/postgres.py`:
- reads `postgres_uri` from settings
- opens a connection per request via `get_db()` (context manager)
- ensures the `dita_tag` table exists (`CREATE TABLE IF NOT EXISTS ...`)

Routes in `app/api/routes.py`:
- `POST /api/insertDitaTag`
  - body is `list[TagItem]`
  - upserts each `{key, value}` into the `ditaTag` collection
- `GET /api/insertDitaTag`
  - returns all tags from `ditaTag` (excluding `_id`)

## Local development

1. Create & activate a venv.
2. Install deps: `python -m pip install -r requirements.txt`
3. Ensure PostgreSQL is reachable via `postgres_uri` (or update `config.yml` / `.env`).
4. Start the server:
   - `python run.py`
   - or `uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload`

## Troubleshooting quick hits

- “PostgreSQL connection failed”: verify `postgres_uri` in `config.yml` and that PostgreSQL is running/reachable.
- “No html files found in zip file.”: ensure the archive contains `*.html`/`*.htm` files (nested is OK).
- “The following files do not contain a title.”: add `<title>...</title>` to each HTML file (preflight enforces it).
- Download link works once, then disappears: the download endpoint clears `downloads_root` as a background task after serving a file.
