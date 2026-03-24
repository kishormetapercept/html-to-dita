from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import time

from app.api.routes import router
from app.constants import messages as msg
from app.core.config import get_settings


settings = get_settings()
app = FastAPI(title=settings.app_name)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger('html_to_dita')


def _gateway_user_context(request: Request) -> dict[str, str]:
    return {
        'id': request.headers.get('x-user-id', ''),
        'login': request.headers.get('x-user-login', ''),
        'name': request.headers.get('x-user-name', ''),
        'email': request.headers.get('x-user-email', ''),
        'profile_url': request.headers.get('x-user-profile-url', ''),
    }


@app.middleware('http')
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    user = _gateway_user_context(request)

    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            'request_failed method=%s path=%s query=%s user_id=%s user_login=%s user_email=%s duration_ms=%.2f',
            request.method,
            request.url.path,
            request.url.query,
            user['id'],
            user['login'],
            user['email'],
            elapsed_ms,
        )
        raise

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        'request method=%s path=%s query=%s status=%s user_id=%s user_login=%s user_email=%s duration_ms=%.2f',
        request.method,
        request.url.path,
        request.url.query,
        response.status_code,
        user['id'],
        user['login'],
        user['email'],
        elapsed_ms,
    )

    if any(user.values()):
        logger.info(
            'gateway_user_details id=%s login=%s name=%s email=%s profile_url=%s',
            user['id'],
            user['login'],
            user['name'],
            user['email'],
            user['profile_url'],
        )

    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, __: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"message": msg.INVALID_REQUEST_PAYLOAD, "status": 422},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
