"""
Bitok API.
`uvicorn app.main:app --reload`
→ http://localhost:8000/docs
"""

import json
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import (
    BitokError,
    bitok_error_handler,
    http_error_handler,
    not_implemented_handler,
    validation_error_handler,
)
from app.domains.auth.router import router as auth_router
from app.domains.check.router import router as check_router
from app.domains.ingredient.router import router as ingredient_router
from app.domains.profile.router import router as profile_router
from app.domains.restaurant.router import router as restaurant_router
from app.domains.saved.router import router as saved_router
from app.domains.scan.router import analysis_router
from app.domains.scan.router import router as scan_router

SWAGGER_UI_PARAMETERS = {
    "persistAuthorization": True,
    "displayRequestDuration": True,
}

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(BitokError, bitok_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(NotImplementedError, not_implemented_handler)

for r in (
    auth_router,
    profile_router,
    ingredient_router,
    restaurant_router,
    scan_router,
    analysis_router,
    check_router,
    saved_router,
):
    app.include_router(r)


@app.get(
    "/health",
    tags=["기타"],
    summary="상태 확인",
    responses={200: {"content": {"application/json": {"example": {"status": "ok"}}}}},
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


_basic = HTTPBasic(auto_error=False)


def require_docs_login(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
) -> None:
    if not settings.docs_password:
        return

    ok = credentials is not None and (
        secrets.compare_digest(credentials.username, settings.docs_username)
        and secrets.compare_digest(credentials.password, settings.docs_password)
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="문서 열람 권한이 없습니다.",
            headers={"WWW-Authenticate": 'Basic realm="Bitok docs"'},
        )


DocsLogin = Annotated[None, Depends(require_docs_login)]


@app.get("/openapi.json", include_in_schema=False)
async def openapi_json(_: DocsLogin) -> dict:
    return app.openapi()


@app.get("/docs", include_in_schema=False)
async def swagger_ui(_: DocsLogin) -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{settings.app_name} - Swagger UI",
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    )


def custom_openapi() -> dict:
    """FastAPI 가 적어 넣는 422(HTTPValidationError)를 실제 응답 모양으로 바꾼다."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        tags=app.openapi_tags,
        routes=app.routes,
    )

    ref = {"$ref": "#/components/schemas/ErrorResponse"}
    for path in schema.get("paths", {}).values():
        for op in path.values():
            r422 = op.get("responses", {}).get("422")
            if not r422:
                continue
            if "HTTPValidationError" in str(r422):  # 자동 생성된 것
                r422["description"] = "요청 형식 오류"
                r422["content"] = {
                    "application/json": {
                        "schema": ref,
                        "example": {
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "요청 형식이 올바르지 않습니다.",
                                "detail": {
                                    "fields": [
                                        {
                                            "location": "device_token",
                                            "where": "body",
                                            "reason": "Field required",
                                        }
                                    ]
                                },
                            }
                        },
                    }
                }
            else:  # 우리가 선언한 422
                r422["description"] += " · 요청 형식 오류"

    schemas = schema.get("components", {}).get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)

    renames = {"Body_create_scan_api_scans_post": "CreateScanForm"}
    for old, new in renames.items():
        if old in schemas:
            schemas[new] = schemas.pop(old)
            schemas[new]["title"] = new

    for s in schemas.values():
        for prop in s.get("properties", {}).values():
            binary = prop.get("contentMediaType") == "application/octet-stream" or (
                prop.get("type") == "string" and prop.get("format") == "binary"
            )
            if binary:
                prop["contentMediaType"] = "application/octet-stream"
                prop["format"] = "binary"

    text = json.dumps(schema)
    for old, new in renames.items():
        text = text.replace(
            f"#/components/schemas/{old}", f"#/components/schemas/{new}"
        )
    schema = json.loads(text)

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
