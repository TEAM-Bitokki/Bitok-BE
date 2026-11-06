from fastapi import APIRouter

from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, responses
from app.domains.profile.schemas import ProfileRequest, ProfileResponse

router = APIRouter(prefix="/api/profiles", tags=["식이 프로필"])


@router.get(
    "/me",
    response_model=ProfileResponse,
    summary="내 식이 프로필 조회",
    responses=responses(UNAUTHORIZED, (404, "프로필 미등록")),
)
async def get_profile(user_id: CurrentUser) -> ProfileResponse:
    raise NotImplementedError


@router.put(
    "/me",
    response_model=ProfileResponse,
    summary="식이 프로필 등록/수정",
    description=(
        "통째로 덮어쓴다. 온보딩과 수정 화면이 같은 엔드포인트를 쓴다.\n\n"
        "알레르기는 고른 성분만 판정한다. 새우를 골라도 새우젓은 걸리지 않는다.\n\n"
        "식단 유형은 분류 단위로 막고, Vegetarian 하위 선택으로 달걀·유제품을 뺀다."
    ),
    responses=responses(UNAUTHORIZED, (400, "존재하지 않는 성분 id")),
)
async def put_profile(body: ProfileRequest, user_id: CurrentUser) -> ProfileResponse:
    raise NotImplementedError
