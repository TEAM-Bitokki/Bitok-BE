"""에러 코드와 응답 규격"""

from enum import StrEnum
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode(StrEnum):
    # 요청
    VALIDATION_ERROR = "VALIDATION_ERROR"  # 422 요청 형식 오류
    INVALID_IMAGE = "INVALID_IMAGE"  # 400 형식 크기 문제
    PROFILE_REQUIRED = "PROFILE_REQUIRED"  # 422 식이 프로필 미등록
    OCR_LOW_CONFIDENCE = "OCR_LOW_CONFIDENCE"  # 422 재촬영 필요
    NO_MENU_DETECTED = "NO_MENU_DETECTED"  # 422 메뉴를 못 읽음

    # 인증
    UNAUTHORIZED = "UNAUTHORIZED"

    # 리소스
    NOT_FOUND = "NOT_FOUND"  # 404
    ALREADY_EXISTS = "ALREADY_EXISTS"  # 409
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"  # 405

    # 한도
    RATE_LIMITED = "RATE_LIMITED"  # 429

    # 외부
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"  # 502 모델 응답 실패
    LLM_TIMEOUT = "LLM_TIMEOUT"  # 504 타임아웃
    MAP_UNAVAILABLE = "MAP_UNAVAILABLE"  # 502 카카오 API 실패

    # 서버
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"  # 501 아직 구현 전


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    detail: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class BitokError(HTTPException):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: dict[str, Any] | None = None,
    ):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.extra = detail


def _body(code: ErrorCode, message: str, detail: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail}}


async def bitok_error_handler(request: Request, exc: BitokError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(exc.code, exc.message, exc.extra),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """기본 422(`{"detail": [...]}`)를 공통 규격으로"""
    fields = [
        {
            "location": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
            "where": e["loc"][0],
            "reason": e["msg"],
        }
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_body(
            ErrorCode.VALIDATION_ERROR,
            "요청 형식이 올바르지 않습니다.",
            {"fields": fields},
        ),
    )


_STATUS_CODES: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHORIZED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.ALREADY_EXISTS,
    429: ErrorCode.RATE_LIMITED,
    501: ErrorCode.NOT_IMPLEMENTED,
}

_STATUS_MESSAGES: dict[int, str] = {
    404: "없는 경로입니다.",
    405: "이 경로에서 지원하지 않는 메서드입니다.",
}


async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """라우팅 단계 에러"""
    code = _STATUS_CODES.get(exc.status_code, ErrorCode.NOT_FOUND)
    message = _STATUS_MESSAGES.get(exc.status_code) or str(exc.detail)
    detail = {"path": request.url.path, "method": request.method}
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(code, message, detail),
        headers=getattr(exc, "headers", None),
    )


async def not_implemented_handler(
    request: Request, exc: NotImplementedError
) -> JSONResponse:
    """구현 전"""
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content=_body(
            ErrorCode.NOT_IMPLEMENTED,
            "아직 구현되지 않은 엔드포인트입니다.",
            {"path": request.url.path, "method": request.method},
        ),
    )


_EXAMPLE_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.UNAUTHORIZED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.ALREADY_EXISTS,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    501: ErrorCode.NOT_IMPLEMENTED,
    502: ErrorCode.LLM_UNAVAILABLE,
    504: ErrorCode.LLM_TIMEOUT,
}


def responses(*specs: tuple) -> dict:
    out = {}
    for spec in specs:
        status_code, desc = spec[0], spec[1]
        code = spec[2] if len(spec) > 2 else _EXAMPLE_CODES[status_code]
        out[status_code] = {
            "model": ErrorResponse,
            "description": desc,
            "content": {
                "application/json": {
                    "example": {"error": {"code": code.value, "message": desc}}
                }
            },
        }
    return out


UNAUTHORIZED = (401, "deviceToken이 올바르지 않음")
