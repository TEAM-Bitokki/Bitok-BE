from fastapi import APIRouter, status

from app.core.db import SessionDep
from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, responses
from app.domains.saved import service
from app.domains.saved.schemas import (
    SavedRestaurantListResponse,
    SaveRestaurantRequest,
)

router = APIRouter(prefix="/api/saved-restaurants", tags=["저장한 식당"])


@router.get(
    "",
    response_model=SavedRestaurantListResponse,
    summary="저장한 식당 목록",
    description="My > Saved Restaurants. 최신순.",
    responses=responses(UNAUTHORIZED),
)
async def list_saved(
    user_id: CurrentUser, session: SessionDep
) -> SavedRestaurantListResponse:
    # CurrentUser가 deviceToken을 검증하고 현재 사용자의 id를 넘겨준다.
    # 라우터는 HTTP 요청만 받고, 실제 조회는 service에 맡긴다.
    return await service.list_saved_restaurants(session, user_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="식당 저장",
    description="이미 저장된 식당이면 409 대신 그대로 성공으로 처리한다.",
    responses=responses(UNAUTHORIZED, (404, "없는 식당")),
)
async def save_restaurant(
    body: SaveRestaurantRequest,
    user_id: CurrentUser,
    session: SessionDep,
) -> None:
    # 요청 본문의 restaurant_id와 로그인 사용자 id를 함께 전달한다.
    # 정상 저장과 중복 저장 모두 응답 본문 없이 201을 반환한다.
    await service.save_restaurant(session, user_id, body.restaurant_id)


@router.delete(
    "/{restaurant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="식당 저장 해제",
    responses=responses(UNAUTHORIZED, (404, "저장되지 않은 식당")),
)
async def unsave_restaurant(
    restaurant_id: int, user_id: CurrentUser, session: SessionDep
) -> None:
    # URL 경로의 restaurant_id를 받아 현재 사용자의 저장 항목만 해제한다.
    # 삭제할 항목이 없으면 service에서 404를 발생시킨다.
    await service.unsave_restaurant(session, user_id, restaurant_id)
