"""인증 의존성"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import APIKeyHeader

from app.core.errors import BitokError, ErrorCode

device_token_scheme = APIKeyHeader(
    name="deviceToken",
    scheme_name="deviceToken",
    auto_error=False,
)

DeviceToken = Annotated[str | None, Depends(device_token_scheme)]


def canonical_uuid4(value: str | None) -> str | None:
    """UUID v4 면 표준 표기로, 아니면 None.
    하이픈이 있든 없든 같은 토큰"""
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    return str(parsed) if parsed.version == 4 else None


_UNAUTHORIZED_MESSAGE = "유효한 deviceToken이 필요합니다."


async def get_current_user_id(device_token: DeviceToken) -> int:
    """토큰으로 users 조회."""

    def unauthorized() -> BitokError:
        return BitokError(
            ErrorCode.UNAUTHORIZED, _UNAUTHORIZED_MESSAGE, status_code=401
        )

    token = canonical_uuid4(device_token)
    if token is None:
        raise unauthorized()

    # TODO: sha256(token) 으로 users 조회
    raise unauthorized()


CurrentUser = Annotated[int, Depends(get_current_user_id)]
