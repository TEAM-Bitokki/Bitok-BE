from fastapi import APIRouter, status

from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, responses
from app.domains.auth.schemas import (
    DeleteAccountRequest,
    MeResponse,
    NicknameRequest,
    SignUpRequest,
)

router = APIRouter(prefix="/api", tags=["계정"])


@router.post(
    "/auth/sign-up",
    response_model=MeResponse,
    status_code=status.HTTP_200_OK,
    summary="가입",
    description=(
        "deviceToken 으로 계정을 만든다.\n\n"
        "이 엔드포인트만 deviceToken 헤더가 필요 없다."
    ),
)
async def sign_up(body: SignUpRequest) -> MeResponse:
    raise NotImplementedError


@router.get(
    "/auth/me",
    response_model=MeResponse,
    summary="내 계정 조회",
    description=(
        "앱 시작 시 호출.\n\n"
        "`has_profile` false -> 온보딩\n\n"
        "등록되지 않은 토큰이면 401 -> 가입 화면."
    ),
    responses=responses(UNAUTHORIZED),
)
async def get_me(user_id: CurrentUser) -> MeResponse:
    raise NotImplementedError


@router.patch(
    "/users/me",
    response_model=MeResponse,
    summary="닉네임 수정",
    responses=responses(UNAUTHORIZED),
)
async def update_nickname(body: NicknameRequest, user_id: CurrentUser) -> MeResponse:
    raise NotImplementedError


@router.delete(
    "/users/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="회원 탈퇴",
    description=(
        "계정과 식이 프로필을 삭제한다.\n\n"
        "`delete_saved_menus`\n"
        "- **true** — 저장한 메뉴와 분석 기록까지 삭제\n"
        "- **false** — 기록은 남기고 사용자 연결만 끊기\n\n"
    ),
    responses=responses(UNAUTHORIZED, (400, "재확인 없이 호출")),
)
async def delete_account(body: DeleteAccountRequest, user_id: CurrentUser) -> None:
    raise NotImplementedError
