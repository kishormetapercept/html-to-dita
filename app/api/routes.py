from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.db.mongo import get_db
from app.models.runtime_state import state_store
from app.schemas.auth import LoginRequest, RegisterRequest, TagItem
from app.services.files import (
    create_zip_archive,
    convert_input_to_dita,
    find_html_files,
    find_html_without_title,
    prepare_user_directories,
    remove_directory,
    remove_user_io_directories,
    resolve_zip_file,
    save_and_extract_zip,
    write_ditamap,
)
from app.services.security import create_access_token, hash_password, verify_password


router = APIRouter()
settings = get_settings()


@router.get("/")
async def health_check():
    return {"message": "Html2Dita Backend", "status": "Online"}


@router.post("/api/pre-flight-check")
async def pre_flight_check(request: Request):
    try:
        form = await request.form()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"message": "Invalid multipart/form-data payload.", "status": 400},
        )

    user_id = str(form.get("userId") or "").strip()
    if not user_id:
        return JSONResponse(status_code=400, content={"message": "userId is required", "status": 400})

    upload = form.get("zipFile") or form.get("file") or form.get("zip")
    if upload is None or not hasattr(upload, "file"):
        return JSONResponse(
            status_code=400,
            content={
                "message": "No zip file provided. Send multipart/form-data with fields: userId (text) and zipFile (file).",
                "status": 400,
            },
        )

    input_dir, output_dir = prepare_user_directories(user_id, settings.input_root, settings.output_root)

    try:
        file_name = save_and_extract_zip(upload, input_dir)
        state_store.set_paths(user_id, input_dir, output_dir)
        state_store.set_input_file_name(user_id, file_name)

        html_files = find_html_files(input_dir)
        if not html_files:
            remove_user_io_directories(user_id, settings.input_root, settings.output_root)
            return JSONResponse(
                status_code=400,
                content={"message": "No html files found in zip file.", "status": 400},
            )

        invalid_files = find_html_without_title(html_files, input_dir)
        if invalid_files:
            remove_user_io_directories(user_id, settings.input_root, settings.output_root)
            return JSONResponse(
                status_code=400,
                content={
                    "message": "The following files do not contain a title.",
                    "status": 400,
                    "invalidFiles": invalid_files,
                },
            )

        return JSONResponse(status_code=201, content={"message": "Ok", "status": 201})
    except ValueError as error:
        remove_user_io_directories(user_id, settings.input_root, settings.output_root)
        return JSONResponse(status_code=400, content={"message": str(error), "status": 400})
    except Exception:
        remove_user_io_directories(user_id, settings.input_root, settings.output_root)
        return JSONResponse(status_code=500, content={"message": "Internal server error", "status": 500})


@router.post("/api/htmltodita")
async def html_to_dita(request: Request):
    user_id = ""
    content_type = request.headers.get("content-type", "").lower()

    try:
        if "application/json" in content_type:
            payload = await request.json()
            user_id = str(payload.get("userId") or "").strip()
        else:
            form = await request.form()
            user_id = str(form.get("userId") or "").strip()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"message": "Invalid request payload.", "status": 400},
        )

    if not user_id:
        return JSONResponse(
            status_code=400,
            content={"message": "userId is required", "status": 400},
        )

    state = state_store.get(user_id)
    input_dir = state.input_dir or str(Path(settings.input_root) / user_id)
    output_dir = state.output_dir or str(Path(settings.output_root) / user_id)

    if not Path(input_dir).exists():
        return JSONResponse(
            status_code=404,
            content={"message": "Please upload zip file first!", "status": 404},
        )

    try:
        dita_files = convert_input_to_dita(input_dir, output_dir)
        if not dita_files:
            return JSONResponse(
                status_code=400,
                content={"message": "No html files found in zip file.", "status": 400},
            )

        write_ditamap(output_dir, dita_files)

        download_id = uuid4().hex
        zip_name = state.input_file_name if state.input_file_name.endswith(".zip") else f"{user_id}.zip"

        download_dir = Path(settings.downloads_root) / download_id
        output_zip_path = download_dir / zip_name
        create_zip_archive(output_dir, str(output_zip_path))

        state_store.set_input_file_name(user_id, zip_name)
        state_store.clear_paths(user_id)
        remove_user_io_directories(user_id, settings.input_root, settings.output_root)

        download_link = f"{settings.base}/api/download/{user_id}/{download_id}"
        return JSONResponse(
            status_code=200,
            content={
                "message": "Files converted successfully.",
                "downloadLink": download_link,
                "status": 200,
            },
        )
    except Exception:
        remove_user_io_directories(user_id, settings.input_root, settings.output_root)
        return JSONResponse(status_code=500, content={"message": "Error processing files.", "status": 500})


@router.get("/api/download/{user_id}/{download_id}")
async def download(user_id: str, download_id: str):
    state = state_store.get(user_id)
    download_dir = Path(settings.downloads_root) / download_id
    zip_file = resolve_zip_file(str(download_dir), state.input_file_name)

    if zip_file is None:
        return JSONResponse(status_code=404, content={"message": "File not found", "status": 404})

    state_store.clear_input_file_name(user_id)
    return FileResponse(
        path=str(zip_file),
        media_type="application/octet-stream",
        filename=zip_file.name,
        background=BackgroundTask(remove_directory, str(download_dir)),
    )


@router.post("/api/register")
async def register(payload: RegisterRequest):
    db = get_db()
    users = db["users"]

    if users.find_one({"email": payload.email}):
        return JSONResponse(status_code=400, content={"message": "User registration failed", "status": 400})

    users.insert_one(
        {
            "email": payload.email,
            "password": hash_password(payload.password),
            "lastLogin": None,
        }
    )
    return JSONResponse(status_code=201, content={"message": "User registered", "status": 201})


@router.post("/api/login")
async def login(payload: LoginRequest):
    db = get_db()
    users = db["users"]
    user = users.find_one({"email": payload.email})

    if user is None or not verify_password(payload.password, user.get("password", "")):
        return JSONResponse(
            status_code=401,
            content={"message": "Invalid email or password", "status": 401},
        )

    users.update_one({"_id": user["_id"]}, {"$currentDate": {"lastLogin": True}})
    token = create_access_token(str(user["_id"]))
    return JSONResponse(
        status_code=200,
        content={
            "message": "Login successful",
            "status": 200,
            "token": token,
            "userId": str(user["_id"]),
        },
    )


@router.post("/api/insertDitaTag")
async def insert_dita_tags(tags: list[TagItem]):
    db = get_db()
    dita_tags = db["ditaTag"]

    for tag in tags:
        dita_tags.update_one({"key": tag.key}, {"$set": {"value": tag.value}}, upsert=True)

    return JSONResponse(
        status_code=200,
        content={"message": "Tags processed successfully", "status": 200},
    )


@router.get("/api/insertDitaTag")
async def get_dita_tags():
    db = get_db()
    tags = [{"key": item["key"], "value": item["value"]} for item in db["ditaTag"].find({}, {"_id": 0})]
    return JSONResponse(
        status_code=201,
        content={"message": "Tags Fetched successfully", "status": 201, "tags": tags},
    )
