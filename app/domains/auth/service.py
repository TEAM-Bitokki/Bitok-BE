"""가입 조회 탈퇴"""

import contextlib

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import unauthorized
from app.core.security import hash_token
from app.core.storage import delete_image
from app.domains.auth.models import User
from app.domains.auth.schemas import MeResponse
from app.domains.check.models import MyCheck
from app.domains.profile.models import DietaryProfile, ProfileAllergen
from app.domains.scan.models import Scan


async def find_user_id(session: AsyncSession, device_token: str) -> int | None:
    """인증에서 쓴다. 토큰은 표준 표기로 들어와야 한다"""
    return await session.scalar(
        select(User.id).where(User.device_token == hash_token(device_token))
    )


async def sign_up(
    session: AsyncSession, device_token: str, nickname: str | None
) -> MeResponse:
    """같은 토큰으로 다시 불러도 기존 계정을 돌려준다"""
    user_id = await find_user_id(session, device_token)
    if user_id is None:
        user = User(device_token=hash_token(device_token), nickname=nickname)
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            user_id = await find_user_id(session, device_token)
            if user_id is None:
                raise
        else:
            user_id = user.id
    return await get_me(session, user_id)


async def get_me(session: AsyncSession, user_id: int) -> MeResponse:
    user = await session.get(User, user_id)
    if user is None:
        # 인증을 지난 뒤 다른 요청이 탈퇴시킨 경우
        raise unauthorized()
    profile = await session.scalar(
        select(DietaryProfile).where(DietaryProfile.user_id == user_id)
    )
    allergen_count = 0
    if profile is not None:
        allergen_count = await session.scalar(
            select(func.count())
            .select_from(ProfileAllergen)
            .where(ProfileAllergen.profile_id == profile.id)
        )
    return MeResponse(
        user_id=user.id,
        nickname=user.nickname,
        has_profile=profile is not None,
        diet_type=profile.diet_type if profile else None,
        allergen_count=allergen_count,
    )


async def delete_account(
    session: AsyncSession, user_id: int, delete_saved_menus: bool
) -> None:
    """users 를 지우면 FK 정책에 따라 나뉜다.
    false면 개인 설정은 사라지고 공개 기록은 익명화되어 남는다."""
    images = []
    if delete_saved_menus:
        images = list(
            await session.scalars(select(Scan.image_url).where(Scan.user_id == user_id))
        )
        await session.execute(delete(MyCheck).where(MyCheck.user_id == user_id))
        await session.execute(delete(Scan).where(Scan.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()
    for key in images:  # 탈퇴는 이미 끝났다. 사진 하나 못 지웠다고 실패로 돌리지 않는다
        with contextlib.suppress(OSError):
            await delete_image(key)
