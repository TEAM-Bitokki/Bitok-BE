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
        "저장할 때 서버가 차단 성분 분류 목록을 계산한다. "
        "고른 알레르기 성분의 분류와 식단 유형에서 나오는 분류를 합치고, "
        "Vegetarian 하위 선택으로 일부를 뺀다.\n\n"
    ),
    responses=responses(UNAUTHORIZED, (400, "존재하지 않는 성분 id")),
)
async def put_profile(body: ProfileRequest, user_id: CurrentUser) -> ProfileResponse:
    raise NotImplementedError
