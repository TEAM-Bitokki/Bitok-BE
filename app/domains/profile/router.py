from fastapi import APIRouter

from app.core.db import SessionDep
from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, responses
from app.domains.profile import service
from app.domains.profile.schemas import ProfileRequest, ProfileResponse

router = APIRouter(prefix="/api/profiles", tags=["식이 프로필"])


@router.get(
    "/me",
    response_model=ProfileResponse,
    summary="내 식이 프로필 조회",
    responses=responses(UNAUTHORIZED, (404, "프로필 미등록")),
)
async def get_profile(user_id: CurrentUser, session: SessionDep) -> ProfileResponse:
    return await service.get_profile(session, user_id)


@router.put(
    "/me",
    response_model=ProfileResponse,
    summary="식이 프로필 등록/수정",
    description=(
        "통째로 덮어쓴다. 온보딩과 수정 화면이 같은 엔드포인트를 쓴다.\n\n"
        "알레르기는 메뉴의 원재료까지 본다. "
        "대두를 고르면 두부가 든 메뉴도, 생선을 고르면 연어가 든 메뉴도 걸린다.\n\n"
        "식단 유형은 분류 단위로 막고, Vegetarian 하위 선택으로 달걀·유제품을 뺀다. "
        "`diet_options` 는 Vegetarian 일 때만 저장된다.\n\n"
        "알레르기를 하나도 안 골라도(Skip) 저장하면 프로필이 생긴다."
    ),
    responses=responses(
        UNAUTHORIZED, (400, "검색 목록에 없는 성분 id. detail.ingredient_ids 에 목록")
    ),
)
async def put_profile(
    body: ProfileRequest, user_id: CurrentUser, session: SessionDep
) -> ProfileResponse:
    return await service.put_profile(session, user_id, body)
