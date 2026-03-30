# HTML‑DITA Converter Project Documentation

## 1. Project Overview

**Project Name:** HTML‑DITA Converter (HTMLTODITA)

The HTML‑DITA Converter project is a FastAPI-based backend application designed to convert HTML documents (uploaded as a ZIP archive) into structured DITA (Darwin Information Typing Architecture) output.

This project is a migration from an earlier Node.js implementation and aims to replicate and enhance the original system’s API and functionality, including live progress updates via Server‑Sent Events (SSE).

The application processes an uploaded ZIP through a multi-stage pipeline that validates the input, converts HTML content into DITA topics and ditamaps, packages the result into a ZIP archive, and returns a downloadable link.

The server is launched using `python run.py` (which starts Uvicorn) or directly via `uvicorn app.main:app`.

## 2. Technology Stack

### Core Technologies

- **Programming Language:** Python
- **Web Framework:** FastAPI  
  FastAPI is a modern, high-performance web framework for building APIs with Python, known for its speed and developer-friendly design.
- **ASGI Server:** Uvicorn  
  ASGI (Asynchronous Server Gateway Interface) is a Python standard for building asynchronous, high-performance web servers and applications.

| Component | Technology | Purpose |
|---|---|---|
| Framework | FastAPI | High-performance API layer (ASGI). |
| Server | Uvicorn | ASGI server implementation. |
| Parsing (HTML) | BeautifulSoup4 | HTML parsing and normalization for conversion. |
| Core utilities | pathlib, zipfile, shutil | Filesystem management and final ZIP bundling. |
| Streaming | SSE (`StreamingResponse`) | Real-time pipeline progress updates. |
| Database | PostgreSQL | Persistent storage for DITA tag mappings. |
| DB driver | pg8000 | PostgreSQL connectivity from Python. |
| Config | pydantic-settings + YAML | Settings via `config.yml`, `.env`, env vars. |

### Standard Python Libraries

- `pathlib` – File system path management
- `zipfile` – ZIP archive extraction/creation
- `shutil` – File and directory operations
- `re`, `html` – text normalization and escaping used in conversion

## 3. API Endpoints

### 3.1 `POST /api/convert`

**Summary:** Upload ZIP and convert HTML → DITA with SSE progress  
**Description:**  
This endpoint initiates the conversion process. It accepts a ZIP file, validates it, converts the HTML content into DITA, packages the output, and streams progress events to the client.

**Request:**
- `Content-Type: multipart/form-data`
- Payload:
  - `zipFile` (file) *(required; legacy accepted: `file`, `zip`)*
  - `userId` (text) *(optional; fallback: `x-user-id` header; final fallback: `default`)*

**Response:**
- `text/event-stream` (SSE)
- Event types: `progress`, `step_completed`, `failed`, `completed`
- On completion, includes `downloadLink`

### 3.2 `POST /api/pre-flight-check`

**Summary:** Backward-compatible alias to convert  
**Description:**  
Alias for `POST /api/convert` to keep existing frontend integrations working.

### 3.3 `GET /api/download/{user_id}/{download_id}`

**Summary:** Download converted DITA package  
**Description:**  
Retrieves the processed output after conversion. All generated files are compressed into a ZIP archive.

**Request:**
- Standard GET request with path params `{user_id}` and `{download_id}`

**Response:**
- `application/octet-stream` ZIP file containing:
  - `.dita` files (topics)
  - `.ditamap` files (maps)
  - `images/` assets (when present)

### 3.4 `GET /`

**Summary:** Health check  
**Response:**
```json
{"message":"Html2Dita Backend","status":"Online"}
```

### 3.5 `POST /api/insertDitaTag`

**Summary:** Upsert DITA tag mappings  
**Description:**  
Upserts tag mappings into PostgreSQL table `dita_tag`.

**Request:**
- `Content-Type: application/json`
- Body: JSON array of `{ "key": string, "value": string }`

**Response:** JSON status message

### 3.6 `GET /api/insertDitaTag`

**Summary:** Fetch all DITA tag mappings  
**Description:**  
Returns all `{key,value}` rows from PostgreSQL table `dita_tag`.

## 4. End-to-End Conversion Flow

The HTML-to-DITA conversion process consists of multiple sequential stages:

### 4.1 Preflight & Validation

**Component:** PreflightService (logical component)  
**What it does:**
- Resolves `user_id` (from form `userId` or header `x-user-id`)
- Creates user-specific input/output directories:
  - `input/<user_id>/`
  - `output/<user_id>/`
- Saves the uploaded ZIP, extracts it into `input/<user_id>/`, then deletes the uploaded ZIP file
- Validates:
  - ZIP contains at least one `*.html` / `*.htm` file
  - every HTML contains a `<title>` tag

### 4.2 HTML → DITA Conversion

**Component:** ConversionService (logical component)  
**What it does:**
- Parses HTML via BeautifulSoup
- Chooses document title:
  - first heading (`h1`..`h6`) when present, else `<title>`, else filename
- Converts content blocks into DITA-ish XML fragments (headings/paragraphs/lists/tables/code/quotes/images)
- Writes:
  - per-input topic folder with topic files
  - per-input `.ditamap`
  - top-level `index.ditamap` referencing all generated outputs
- Copies supported images to `output/<user_id>/images/`

### 4.3 Final Packaging

**Component:** DownloadService (logical component)  
**What it does:**
- Creates `downloads/<download_id>/<zip_name>` by zipping `output/<user_id>/`
- Emits final SSE `completed` event including a `downloadLink`

### 4.4 Cleanup

After success or failure:
- Removes `input/<user_id>/` and `output/<user_id>/`
- After a download is served, a background cleanup clears `downloads_root`

## 5. Conversion Pipeline Architecture

The current codebase is implemented as functions, but it maps to these pipeline roles:

### Core Services

#### PreflightService – Validation & setup

- **Responsibilities:** directory setup, ZIP extraction, HTML file discovery, `<title>` validation
- **Key code locations:**
  - `app/api/routes.py:_pipeline_event_stream` (preflight phase)
  - `app/services/files.py:prepare_user_directories`
  - `app/services/files.py:save_and_extract_zip`
  - `app/services/files.py:find_html_files`
  - `app/services/files.py:find_html_without_title`

#### ConversionService – Pipeline trigger

- **Responsibilities:** start conversion stream and trigger pipeline execution
- **Key code locations:**
  - `app/api/routes.py:convert`
  - `app/api/routes.py:_pipeline_event_stream`

#### PipelineOrchestrator – Controls execution flow

- **Responsibilities:** executes phases in order, emits SSE events, updates `steps`, handles errors, ensures cleanup
- **Key code locations:**
  - `app/api/routes.py:_pipeline_event_stream`

#### DownloadService – Packaging & delivery

- **Responsibilities:** zip creation, download serving, download cleanup
- **Key code locations:**
  - `app/services/files.py:create_zip_archive`
  - `app/api/routes.py:download`
  - `app/services/files.py:resolve_zip_file`
  - `app/services/files.py:clear_directory_contents`

## 6. Module Documentation: `app/services/files.py`

### 6.1 Module Overview

`app/services/files.py` is the core conversion and file-handling module. It provides the end-to-end helpers used by the API layer:

- user directory setup and cleanup
- ZIP extraction and validation helpers
- HTML discovery and title validation
- HTML → DITA conversion and ditamap generation
- ZIP packaging for downloads

### 6.2 Role in Pipeline

This module is called by `app/api/routes.py` during:

- upload extraction (`save_and_extract_zip`)
- preflight checks (`find_html_files`, `find_html_without_title`)
- transformation (`convert_input_to_dita`, `convert_html_file_to_dita`, `write_ditamap`)
- packaging (`create_zip_archive`)

### 6.3 Main Functions

**`save_and_extract_zip(upload, input_dir) -> str`**  
- Validates `.zip`, saves to disk, extracts to `input_dir`, deletes the saved ZIP.

**`convert_input_to_dita(input_dir, output_dir) -> list[str]`**  
- Converts all HTML in `input_dir` into DITA output under `output_dir` and copies images.

**`convert_html_file_to_dita(html_path, dita_path, output_root) -> list[str]`**  
- Converts a single HTML document into a topic folder containing:
  - main topic `.dita`
  - section topics (when present)
  - per-input `.ditamap`

**`write_ditamap(output_dir, dita_rel_paths)`**  
- Writes a top-level `index.ditamap` referencing generated outputs.

## 7. System Workflow Summary

1. User uploads a ZIP of HTML via `POST /api/convert`.
2. File is validated and extracted into `input/<user_id>/`.
3. Preflight validation ensures HTML files exist and contain `<title>`.
4. HTML is converted into DITA topic folders and ditamaps under `output/<user_id>/`.
5. Output is packaged into `downloads/<download_id>/<zip_name>`.
6. User downloads the result via `GET /api/download/{user_id}/{download_id}`.
7. Temporary directories are cleaned up (`input/`, `output/`, and downloads cleanup after serving).

## 8. Key Features

- Fully automated HTML → DITA conversion
- Streaming progress updates via SSE
- Structured topic + map generation
- DITA tag mapping persistence in PostgreSQL (`dita_tag`)
- Simple filesystem-based staging (`input/` → `output/` → `downloads/`)

## 9. Conclusion

The HTML‑DITA Converter provides a robust, modular backend for converting zipped HTML content into structured, downloadable DITA packages. Its pipeline-oriented design (preflight validation → transformation → packaging) combined with SSE progress streaming improves usability for UI clients, while PostgreSQL-backed tag mapping enables consistent metadata/tag management across conversions.

