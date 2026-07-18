import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.errors import AppError, register_error_handlers
from app.api.middleware import RequestIdMiddleware


class CountPayload(BaseModel):
    count: int = Field(ge=1)


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/known-error")
    async def known_error() -> None:
        raise AppError(code="not_ready", message="资源尚未准备完成", status_code=409)

    @app.post("/validation-error")
    async def validation_error(_: CountPayload) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/database-conflict")
    async def database_conflict() -> None:
        raise IntegrityError("insert", {}, ValueError("duplicate secret detail"))

    @app.get("/unknown-error")
    async def unknown_error() -> None:
        raise RuntimeError("internal stack detail")

    return app


@pytest.mark.asyncio
async def test_app_error_uses_stable_contract() -> None:
    app = build_test_app()
    request_id = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/known-error", headers={"X-Request-ID": request_id})

    assert response.status_code == 409
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "code": "not_ready",
        "message": "资源尚未准备完成",
        "request_id": request_id,
        "field_errors": {},
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_validation_errors_are_grouped_by_field() -> None:
    app = build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/validation-error", json={"count": 0})

    payload = response.json()
    assert response.status_code == 422
    assert payload["code"] == "validation_error"
    assert "body.count" in payload["field_errors"]
    assert payload["retryable"] is False


@pytest.mark.asyncio
async def test_framework_404_uses_the_same_error_contract() -> None:
    app = build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/missing")

    payload = response.json()
    assert response.status_code == 404
    assert payload["code"] == "resource_not_found"
    assert payload["message"] == "请求的资源不存在"
    assert payload["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status_code", "code"),
    [
        ("/database-conflict", 409, "database_conflict"),
        ("/unknown-error", 500, "internal_error"),
    ],
)
async def test_internal_errors_do_not_leak_details(
    path: str,
    status_code: int,
    code: str,
) -> None:
    app = build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(path)

    body = response.text.lower()
    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert "secret detail" not in body
    assert "stack detail" not in body
    assert "traceback" not in body
