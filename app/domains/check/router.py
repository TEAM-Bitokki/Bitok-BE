from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.db import SessionDep
from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, ErrorCode, responses
from app.domains.check import service
from app.domains.check.schemas import MyCheckListResponse, SaveRequest

router = APIRouter(prefix="/api/my-checks", tags=["저장한 메뉴"])


@router.get(
    "",
    response_model=MyCheckListResponse,
    summary="저장한 메뉴 목록 조회",
    description=(
        "최신순으로 반환한다. `verdict` 는 현재 프로필 기준으로 다시 계산한 값으로 알레르기를 추가하면 바뀜."
        "알레르기를 추가하면 바뀐다."
    ),
    responses=responses(
        UNAUTHORIZED,
        (
            422,
            "식이 프로필 미등록 또는 잘못된 커서",
            ErrorCode.PROFILE_REQUIRED,
            ErrorCode.VALIDATION_ERROR,
        ),
    ),
)
async def list_my_checks(
    user_id: CurrentUser,
    session: SessionDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MyCheckListResponse:
    # 라우터는 인증·쿼리 파라미터를 받고, 조회와 판정은 service에 맡긴다.
    return await service.list_my_checks(session, user_id, cursor, limit)


@router.post(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="결과 저장",
    description=(
        "스캔에서 분석한 메뉴를 전부 저장한다. 같은 스캔을 다시 저장하면 그대로 둔다.\n\n"
        "같은 메뉴라도 다른 스캔이면 따로 저장 (레시피가 바뀔 수 있잖음)\n\n"
        "저장하지 않아도 분석 기록은 남아 지도와 식당 상세에 반영된다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 스캔 또는 아직 분석 전")),
)
async def save(body: SaveRequest, user_id: CurrentUser, session: SessionDep) -> None:
    await service.save(session, user_id, body.scan_id)
