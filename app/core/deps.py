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


def is_uuid4(value: str) -> bool:
    try:
        return UUID(value).version == 4
    except (ValueError, AttributeError, TypeError):
        return False


_UNAUTHORIZED_MESSAGE = "유효한 deviceToken이 필요합니다."


async def get_current_user_id(device_token: DeviceToken) -> int:
    """토큰으로 users 조회."""

    def unauthorized() -> BitokError:
        return BitokError(
            ErrorCode.UNAUTHORIZED, _UNAUTHORIZED_MESSAGE, status_code=401
        )

    if not device_token or not is_uuid4(device_token):
        raise unauthorized()

    # TODO: users 조회
    raise unauthorized()


CurrentUser = Annotated[int, Depends(get_current_user_id)]
