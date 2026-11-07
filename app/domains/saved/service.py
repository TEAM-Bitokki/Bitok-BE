"""저장한 식당 조회·저장·해제"""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BitokError, ErrorCode
from app.domains.restaurant.models import Restaurant
from app.domains.restaurant.schemas import RestaurantBrief
from app.domains.saved.models import SavedRestaurant
from app.domains.saved.schemas import SavedRestaurantListResponse


def _brief(restaurant: Restaurant) -> RestaurantBrief:
    """저장 목록에서 사용하는 공통 식당 응답을 만든다."""
    return RestaurantBrief(
        restaurant_id=restaurant.id,
        kakao_place_id=restaurant.kakao_place_id,
        name=restaurant.name,
        category=restaurant.category,
        address=restaurant.address,
        lat=float(restaurant.lat),
        lng=float(restaurant.lng),
        checked_menu_count=restaurant.checked_menu_count,
        is_saved=True,
    )


async def list_saved_restaurants(
    session: AsyncSession, user_id: int
) -> SavedRestaurantListResponse:
    """현재 사용자가 저장한 식당을 최근 저장한 순서로 반환한다."""
    restaurants = await session.scalars(
        select(Restaurant)
        .join(
            SavedRestaurant,
            SavedRestaurant.restaurant_id == Restaurant.id,
        )
        .where(SavedRestaurant.user_id == user_id)
        .order_by(SavedRestaurant.saved_at.desc(), SavedRestaurant.id.desc())
    )
    return SavedRestaurantListResponse(items=[_brief(r) for r in restaurants])


async def save_restaurant(
    session: AsyncSession, user_id: int, restaurant_id: int
) -> None:
    """식당을 저장한다. 같은 식당을 다시 저장해도 성공으로 처리한다."""
    restaurant_exists = await session.scalar(
        select(Restaurant.id).where(Restaurant.id == restaurant_id)
    )
    if restaurant_exists is None:
        raise BitokError(ErrorCode.NOT_FOUND, "없는 식당입니다.", status_code=404)

    # UNIQUE(user_id, restaurant_id) 충돌을 무시해 동시 중복 요청도 한 행만 남긴다.
    await session.execute(
        insert(SavedRestaurant)
        .values(user_id=user_id, restaurant_id=restaurant_id)
        .on_conflict_do_nothing(constraint="uq_saved_restaurant")
    )
    await session.commit()


async def unsave_restaurant(
    session: AsyncSession, user_id: int, restaurant_id: int
) -> None:
    """현재 사용자의 저장 항목만 삭제한다."""
    deleted_id = await session.scalar(
        delete(SavedRestaurant)
        .where(
            SavedRestaurant.user_id == user_id,
            SavedRestaurant.restaurant_id == restaurant_id,
        )
        .returning(SavedRestaurant.id)
    )
    if deleted_id is None:
        raise BitokError(
            ErrorCode.NOT_FOUND,
            "저장되지 않은 식당입니다.",
            status_code=404,
        )
    await session.commit()
