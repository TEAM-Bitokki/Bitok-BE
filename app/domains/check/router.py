from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, responses
from app.domains.check.schemas import MyCheckListResponse, SaveRequest

router = APIRouter(prefix="/api/my-checks", tags=["저장한 메뉴"])


@router.get(
    "",
    response_model=MyCheckListResponse,
    summary="저장한 메뉴 목록 조회",
    description=(
        "최신순으로 반환한다. `verdict` 는 현재 프로필 기준으로 다시 계산한 값으로 알레르기를 추가하면 바뀜."
    ),
    responses=responses(UNAUTHORIZED),
)
async def list_my_checks(
    user_id: CurrentUser,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MyCheckListResponse:
    raise NotImplementedError


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="결과 저장",
    description=(
        "분석된 메뉴 전부가 저장된다.\n\n"
        "레시피 변경을 고려하여 중복 저장을 허용한다.\n\n"
        "저장하지 않아도 분석 기록 자체는 남아 지도와 식당 상세에 반영된다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 스캔")),
)
async def save(body: SaveRequest, user_id: CurrentUser) -> None:
    raise NotImplementedError


@router.delete(
    "/{my_check_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="저장 취소",
    responses=responses(UNAUTHORIZED, (404, "없는 항목")),
)
async def unsave(my_check_id: int, user_id: CurrentUser) -> None:
    raise NotImplementedError
