from fastapi import APIRouter, status

from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, responses
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
async def list_saved(user_id: CurrentUser) -> SavedRestaurantListResponse:
    raise NotImplementedError


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
) -> None:
    raise NotImplementedError


@router.delete(
    "/{restaurant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="식당 저장 해제",
    responses=responses(UNAUTHORIZED, (404, "저장되지 않은 식당")),
)
async def unsave_restaurant(restaurant_id: int, user_id: CurrentUser) -> None:
    raise NotImplementedError
