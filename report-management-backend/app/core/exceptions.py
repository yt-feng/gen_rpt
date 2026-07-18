from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.core.responses import error_response
from app.logging.logger import logger
from app.core.config import settings


def _cors_headers(request: Request) -> dict[str, str]:
    """Keep browser-visible API errors readable even for outer server errors."""
    origin = request.headers.get("origin")
    if not origin:
        return {}
    allowed = {value.strip() for value in settings.CORS_ORIGINS.split(",") if value.strip()}
    allowed.add("https://gen-rpt-review-frontend.pages.dev")
    if "*" not in allowed and origin not in allowed:
        return {}
    headers = {
        "Access-Control-Allow-Origin": "*" if "*" in allowed else origin,
        "Vary": "Origin",
    }
    if "*" not in allowed:
        headers["Access-Control-Allow-Credentials"] = "true"
    return headers

async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        headers=_cors_headers(request),
        content=error_response(
            message=str(exc.detail),
        ).model_dump(mode="json")
    )

async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"loc": err["loc"], "msg": err["msg"], "type": err["type"]} for err in exc.errors()]
    return JSONResponse(
        status_code=422,
        headers=_cors_headers(request),
        content=error_response(
            message="Validation error",
            errors=errors
        ).model_dump(mode="json")
    )

async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error(f"Database error: {exc}")
    return JSONResponse(
        status_code=500,
        headers=_cors_headers(request),
        content=error_response(
            message="Internal database error occurred",
        ).model_dump(mode="json")
    )

async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception occurred")
    return JSONResponse(
        status_code=500,
        headers=_cors_headers(request),
        content=error_response(
            message="An unexpected error occurred",
        ).model_dump(mode="json")
    )

def register_exception_handlers(app):
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
