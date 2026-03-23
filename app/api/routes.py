import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.constants import messages as msg
from app.core.config import get_settings
from app.db.mongo import get_db
from app.schemas.auth import TagItem
from app.services.files import (
    create_zip_archive,
    convert_input_to_dita,
    find_html_files,
    find_html_without_title,
    prepare_user_directories,
    clear_directory_contents,
    remove_user_io_directories,
    resolve_zip_file,
    save_and_extract_zip,
    write_ditamap,
)


router = APIRouter()
settings = get_settings()
DEFAULT_USER_ID = "default"
STEP_UPLOAD = "uploadFiles"
STEP_PREFLIGHT = "preFlightCheck"
STEP_TRANSFORMATION = "transformation"
STEP_POSTFLIGHT = "postFlightCheck"
STEP_DOWNLOAD = "download"
STREAM_EVENT_DELAY_SECONDS = 2.0


STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _resolve_user_id(raw_user_id: str) -> str:
    user_id = (raw_user_id or "").strip()
    return user_id or DEFAULT_USER_ID


def _initial_steps() -> dict:
    return {
        STEP_UPLOAD: False,
        STEP_PREFLIGHT: False,
        STEP_TRANSFORMATION: False,
        STEP_POSTFLIGHT: False,
        STEP_DOWNLOAD: False,
    }


def _response(status_code: int, message: str, **extra):
    payload = {"message": message, "status": status_code}
    payload.update(extra)
    return JSONResponse(status_code=status_code, content=payload)


def _cleanup_user_data(user_id: str) -> None:
    remove_user_io_directories(user_id, settings.input_root, settings.output_root)


def _sse_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


def _streaming_response(generator):
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


def _step_payload(
    *,
    message: str,
    status: int,
    steps: dict,
    current_step: str,
    user_id: str,
    job_state: str,
    failed_step: str | None = None,
    download_link: str | None = None,
    invalid_files: list | None = None,
) -> dict:
    payload = {
        "message": message,
        "status": status,
        "userId": user_id,
        "jobState": job_state,
        "currentStep": current_step,
        "steps": steps,
    }
    if failed_step:
        payload["failedStep"] = failed_step
    if download_link:
        payload["downloadLink"] = download_link
    if invalid_files:
        payload["invalidFiles"] = invalid_files
    return payload


async def _single_event_stream(event_type: str, payload: dict):
    await asyncio.sleep(STREAM_EVENT_DELAY_SECONDS)
    yield _sse_event(event_type, payload)


async def _pipeline_event_stream(user_id: str, file_name: str, input_dir: str, output_dir: str):
    steps = _initial_steps()
    steps[STEP_UPLOAD] = True

    current_step = STEP_PREFLIGHT
    yield _sse_event(
        "progress",
        _step_payload(
            message=msg.PREFLIGHT_CHECK_IN_PROGRESS,
            status=202,
            steps=steps,
            current_step=current_step,
            user_id=user_id,
            job_state="running",
        ),
    )
    await asyncio.sleep(STREAM_EVENT_DELAY_SECONDS)

    try:
        html_files = find_html_files(input_dir)
        if not html_files:
            _cleanup_user_data(user_id)
            yield _sse_event(
                "failed",
                _step_payload(
                    message=msg.NO_HTML_FILES_FOUND,
                    status=400,
                    steps=steps,
                    current_step=current_step,
                    user_id=user_id,
                    job_state="failed",
                    failed_step=current_step,
                ),
            )
            return

        invalid_files = find_html_without_title(html_files, input_dir)
        if invalid_files:
            _cleanup_user_data(user_id)
            yield _sse_event(
                "failed",
                _step_payload(
                    message=msg.FILES_WITHOUT_TITLE,
                    status=400,
                    steps=steps,
                    current_step=current_step,
                    user_id=user_id,
                    job_state="failed",
                    failed_step=current_step,
                    invalid_files=invalid_files,
                ),
            )
            return

        steps[STEP_PREFLIGHT] = True
        yield _sse_event(
            "step_completed",
            _step_payload(
                message=msg.PREFLIGHT_CHECK_COMPLETED,
                status=202,
                steps=steps,
                current_step=STEP_PREFLIGHT,
                user_id=user_id,
                job_state="running",
            ),
        )
        await asyncio.sleep(STREAM_EVENT_DELAY_SECONDS)

        current_step = STEP_TRANSFORMATION
        yield _sse_event(
            "progress",
            _step_payload(
                message=msg.TRANSFORMATION_IN_PROGRESS,
                status=202,
                steps=steps,
                current_step=current_step,
                user_id=user_id,
                job_state="running",
            ),
        )
        await asyncio.sleep(STREAM_EVENT_DELAY_SECONDS)

        dita_files = convert_input_to_dita(input_dir, output_dir)
        if not dita_files:
            _cleanup_user_data(user_id)
            yield _sse_event(
                "failed",
                _step_payload(
                    message=msg.NO_HTML_FILES_FOUND,
                    status=400,
                    steps=steps,
                    current_step=current_step,
                    user_id=user_id,
                    job_state="failed",
                    failed_step=current_step,
                ),
            )
            return

        write_ditamap(output_dir, dita_files)
        steps[STEP_TRANSFORMATION] = True
        yield _sse_event(
            "step_completed",
            _step_payload(
                message=msg.TRANSFORMATION_COMPLETED,
                status=202,
                steps=steps,
                current_step=STEP_TRANSFORMATION,
                user_id=user_id,
                job_state="running",
            ),
        )
        await asyncio.sleep(STREAM_EVENT_DELAY_SECONDS)

        current_step = STEP_POSTFLIGHT
        yield _sse_event(
            "progress",
            _step_payload(
                message=msg.POSTFLIGHT_CHECK_IN_PROGRESS,
                status=202,
                steps=steps,
                current_step=current_step,
                user_id=user_id,
                job_state="running",
            ),
        )
        await asyncio.sleep(STREAM_EVENT_DELAY_SECONDS)

        download_id = uuid4().hex
        zip_name = file_name if file_name.endswith(".zip") else f"{user_id}.zip"

        download_dir = Path(settings.downloads_root) / download_id
        output_zip_path = download_dir / zip_name
        create_zip_archive(output_dir, str(output_zip_path))

        steps[STEP_POSTFLIGHT] = True
        yield _sse_event(
            "step_completed",
            _step_payload(
                message=msg.POSTFLIGHT_CHECK_COMPLETED,
                status=202,
                steps=steps,
                current_step=STEP_POSTFLIGHT,
                user_id=user_id,
                job_state="running",
            ),
        )
        await asyncio.sleep(STREAM_EVENT_DELAY_SECONDS)

        steps[STEP_DOWNLOAD] = True

        _cleanup_user_data(user_id)

        download_link = f"{settings.base}/api/download/{user_id}/{download_id}"
        current_step = STEP_DOWNLOAD
        yield _sse_event(
            "completed",
            _step_payload(
                message=msg.FILES_CONVERTED_SUCCESSFULLY,
                status=200,
                steps=steps,
                current_step=current_step,
                user_id=user_id,
                job_state="completed",
                download_link=download_link,
            ),
        )
    except ValueError as error:
        _cleanup_user_data(user_id)
        yield _sse_event(
            "failed",
            _step_payload(
                message=str(error),
                status=400,
                steps=steps,
                current_step=current_step,
                user_id=user_id,
                job_state="failed",
                failed_step=current_step,
            ),
        )
    except Exception:
        _cleanup_user_data(user_id)
        yield _sse_event(
            "failed",
            _step_payload(
                message=msg.ERROR_PROCESSING_FILES,
                status=500,
                steps=steps,
                current_step=current_step,
                user_id=user_id,
                job_state="failed",
                failed_step=current_step,
            ),
        )


@router.get("/")
async def health_check():
    return {"message": msg.APP_NAME, "status": msg.APP_STATUS_ONLINE}


@router.post("/api/convert")
async def convert(request: Request):
    try:
        form = await request.form()
    except Exception:
        return _streaming_response(
            _single_event_stream(
                "failed",
                _step_payload(
                    message=msg.INVALID_MULTIPART_PAYLOAD,
                    status=400,
                    steps=_initial_steps(),
                    current_step=STEP_UPLOAD,
                    user_id=DEFAULT_USER_ID,
                    job_state="failed",
                    failed_step=STEP_UPLOAD,
                ),
            )
        )

    user_id = _resolve_user_id(str(form.get("userId") or ""))
    upload = form.get("zipFile") or form.get("file") or form.get("zip")
    if upload is None or not hasattr(upload, "file"):
        return _streaming_response(
            _single_event_stream(
                "failed",
                _step_payload(
                    message=msg.NO_ZIP_FILE_PROVIDED,
                    status=400,
                    steps=_initial_steps(),
                    current_step=STEP_UPLOAD,
                    user_id=user_id,
                    job_state="failed",
                    failed_step=STEP_UPLOAD,
                ),
            )
        )

    input_dir, output_dir = prepare_user_directories(user_id, settings.input_root, settings.output_root)

    try:
        file_name = save_and_extract_zip(upload, input_dir)
    except ValueError as error:
        _cleanup_user_data(user_id)
        return _streaming_response(
            _single_event_stream(
                "failed",
                _step_payload(
                    message=str(error),
                    status=400,
                    steps=_initial_steps(),
                    current_step=STEP_UPLOAD,
                    user_id=user_id,
                    job_state="failed",
                    failed_step=STEP_UPLOAD,
                ),
            )
        )
    except Exception:
        _cleanup_user_data(user_id)
        return _streaming_response(
            _single_event_stream(
                "failed",
                _step_payload(
                    message=msg.INTERNAL_SERVER_ERROR,
                    status=500,
                    steps=_initial_steps(),
                    current_step=STEP_UPLOAD,
                    user_id=user_id,
                    job_state="failed",
                    failed_step=STEP_UPLOAD,
                ),
            )
        )

    return _streaming_response(_pipeline_event_stream(user_id, file_name, input_dir, output_dir))


@router.post("/api/pre-flight-check")
async def pre_flight_check(request: Request):
    # Backward-compatible alias to keep existing frontend path working.
    return await convert(request)


@router.get("/api/download/{user_id}/{download_id}")
async def download(user_id: str, download_id: str):
    _ = user_id
    download_dir = Path(settings.downloads_root) / download_id
    zip_file = resolve_zip_file(str(download_dir), None)

    if zip_file is None:
        return _response(404, msg.FILE_NOT_FOUND)

    return FileResponse(
        path=str(zip_file),
        media_type="application/octet-stream",
        filename=zip_file.name,
        background=BackgroundTask(clear_directory_contents, settings.downloads_root),
    )


@router.post("/api/insertDitaTag")
async def insert_dita_tags(tags: list[TagItem]):
    db = get_db()
    dita_tags = db["ditaTag"]

    for tag in tags:
        dita_tags.update_one({"key": tag.key}, {"$set": {"value": tag.value}}, upsert=True)

    return _response(200, msg.TAGS_PROCESSED_SUCCESSFULLY)


@router.get("/api/insertDitaTag")
async def get_dita_tags():
    db = get_db()
    tags = [{"key": item["key"], "value": item["value"]} for item in db["ditaTag"].find({}, {"_id": 0})]
    return _response(201, msg.TAGS_FETCHED_SUCCESSFULLY, tags=tags)
