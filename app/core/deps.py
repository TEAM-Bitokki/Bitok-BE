"""인증 의존성"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import APIKeyHeader

from app.core.db import SessionDep
from app.core.errors import unauthorized

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


async def get_current_user_id(device_token: DeviceToken, session: SessionDep) -> int:
    """토큰으로 users 조회"""
    # 순환 import 를 피해 함수 안에서 가져온다
    from app.domains.auth.service import find_user_id

    token = canonical_uuid4(device_token)
    if token is None:
        raise unauthorized()

    user_id = await find_user_id(session, token)
    if user_id is None:
        raise unauthorized()
    return user_id


CurrentUser = Annotated[int, Depends(get_current_user_id)]
