from collections import defaultdict

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    field_errors: dict[str, list[str]] = Field(default_factory=dict)
    retryable: bool = False


class AppError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
        field_errors: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.field_errors = field_errors or {}


class ProviderUnavailableError(AppError):
    def __init__(self, message: str = "模型服务暂时不可用") -> None:
        super().__init__(
            code="provider_unavailable",
            message=message,
            status_code=503,
            retryable=True,
        )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _response(
    *,
    request: Request,
    code: str,
    message: str,
    status_code: int,
    retryable: bool = False,
    field_errors: dict[str, list[str]] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        code=code,
        message=message,
        request_id=_request_id(request),
        field_errors=field_errors or {},
        retryable=retryable,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _response(
        request=request,
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        retryable=exc.retryable,
        field_errors=exc.field_errors,
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    field_errors: defaultdict[str, list[str]] = defaultdict(list)
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        field_errors[location].append(str(error["msg"]))
    return _response(
        request=request,
        code="validation_error",
        message="请求参数不符合要求",
        status_code=422,
        field_errors=dict(field_errors),
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning(
        "database_integrity_error",
        request_id=_request_id(request),
        error_type=type(exc.orig).__name__,
    )
    return _response(
        request=request,
        code="database_conflict",
        message="数据与现有记录冲突",
        status_code=409,
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    error_map = {
        401: ("unauthorized", "需要登录后才能继续"),
        403: ("forbidden", "没有权限执行此操作"),
        404: ("resource_not_found", "请求的资源不存在"),
        405: ("method_not_allowed", "请求方法不受支持"),
    }
    code, message = error_map.get(exc.status_code, ("http_error", "请求无法完成"))
    return _response(
        request=request,
        code=code,
        message=message,
        status_code=exc.status_code,
        retryable=exc.status_code >= 500,
    )


async def unknown_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_request_error",
        request_id=_request_id(request),
        error_type=type(exc).__name__,
    )
    return _response(
        request=request,
        code="internal_error",
        message="服务器暂时无法完成请求",
        status_code=500,
        retryable=True,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, integrity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unknown_error_handler)
