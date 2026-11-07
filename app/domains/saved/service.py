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
    """DB의 Restaurant 객체를 프론트에 전달할 식당 응답으로 변환한다."""
    return RestaurantBrief(
        # DB의 기본키를 앱에서 식당 상세 화면으로 이동할 때 사용한다.
        restaurant_id=restaurant.id,
        kakao_place_id=restaurant.kakao_place_id,
        name=restaurant.name,
        category=restaurant.category,
        address=restaurant.address,
        # PostgreSQL NUMERIC은 Decimal로 조회되므로 JSON 숫자로 직렬화하기 위해 바꾼다.
        lat=float(restaurant.lat),
        lng=float(restaurant.lng),
        checked_menu_count=restaurant.checked_menu_count,
        # 이 함수는 저장 목록에 포함된 식당에만 사용하므로 항상 true다.
        is_saved=True,
    )


async def list_saved_restaurants(
    session: AsyncSession, user_id: int
) -> SavedRestaurantListResponse:
    """현재 사용자가 저장한 식당을 최근 저장한 순서로 반환한다."""
    restaurants = await session.scalars(
        select(Restaurant)
        # saved_restaurants에는 식당 이름과 주소가 없으므로 restaurants와 연결한다.
        .join(
            SavedRestaurant,
            SavedRestaurant.restaurant_id == Restaurant.id,
        )
        # 다른 사용자의 저장 목록이 섞이지 않도록 로그인 사용자 id로 제한한다.
        .where(SavedRestaurant.user_id == user_id)
        # 저장 시간이 같을 때도 결과가 일정하도록 id를 두 번째 정렬 기준으로 둔다.
        .order_by(SavedRestaurant.saved_at.desc(), SavedRestaurant.id.desc())
    )
    # 저장 항목이 없으면 빈 리스트가 만들어져 {"items": []}가 반환된다.
    return SavedRestaurantListResponse(items=[_brief(r) for r in restaurants])


async def save_restaurant(
    session: AsyncSession, user_id: int, restaurant_id: int
) -> None:
    """식당을 저장한다. 같은 식당을 다시 저장해도 성공으로 처리한다."""
    # FK 오류를 그대로 노출하지 않고 명세의 404 응답을 주기 위해 먼저 확인한다.
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
    # INSERT 또는 중복 무시 결과를 하나의 트랜잭션으로 확정한다.
    await session.commit()


async def unsave_restaurant(
    session: AsyncSession, user_id: int, restaurant_id: int
) -> None:
    """현재 사용자의 저장 항목만 삭제한다."""
    deleted_id = await session.scalar(
        delete(SavedRestaurant)
        .where(
            # restaurant_id만 조건으로 쓰면 다른 사용자의 저장도 지워질 수 있다.
            # 로그인 사용자 id를 함께 비교해 본인의 저장 항목만 삭제한다.
            SavedRestaurant.user_id == user_id,
            SavedRestaurant.restaurant_id == restaurant_id,
        )
        # 실제로 삭제된 행이 있는지 추가 SELECT 없이 확인한다.
        .returning(SavedRestaurant.id)
    )
    if deleted_id is None:
        # 해당 식당이 존재하더라도 현재 사용자가 저장하지 않았다면 404다.
        raise BitokError(
            ErrorCode.NOT_FOUND,
            "저장되지 않은 식당입니다.",
            status_code=404,
        )
    # 삭제가 확인된 경우에만 트랜잭션을 확정한다.
    await session.commit()
