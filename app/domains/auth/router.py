from fastapi import APIRouter, status

from app.core.db import SessionDep
from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, BitokError, ErrorCode, responses
from app.domains.auth import service
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
async def sign_up(body: SignUpRequest, session: SessionDep) -> MeResponse:
    return await service.sign_up(session, body.device_token, body.nickname)


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
async def get_me(user_id: CurrentUser, session: SessionDep) -> MeResponse:
    return await service.get_me(session, user_id)


@router.patch(
    "/users/me",
    response_model=MeResponse,
    summary="닉네임 수정",
    responses=responses(UNAUTHORIZED),
)
async def update_nickname(
    body: NicknameRequest, user_id: CurrentUser, session: SessionDep
) -> MeResponse:
    return await service.update_nickname(session, user_id, body.nickname)


@router.delete(
    "/users/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="회원 탈퇴",
    description=(
        "계정·식이 프로필·저장한 식당은 삭제된다.\n\n"
        "`delete_saved_menus` 는 분석 기록만 가른다.\n"
        "- **false** — 익명화해서 남긴다. 식당 상세·지도에 계속 반영된다\n"
        "- **true** — 저장한 메뉴와 분석 기록까지 삭제. 식당 상세·지도에서도 사라진다\n\n"
    ),
    responses=responses(UNAUTHORIZED, (400, "재확인 없이 호출")),
)
async def delete_account(
    body: DeleteAccountRequest, user_id: CurrentUser, session: SessionDep
) -> None:
    if not body.agree:
        raise BitokError(
            ErrorCode.VALIDATION_ERROR, "탈퇴 동의가 필요합니다.", status_code=400
        )
    await service.delete_account(session, user_id, body.delete_saved_menus)
