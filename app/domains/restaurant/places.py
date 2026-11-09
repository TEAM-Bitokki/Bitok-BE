"""지도. 식당 등록, 주변 식당, 이름 검색"""

import math

from sqlalchemy import Float, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import kakao
from app.core.errors import BitokError, ErrorCode
from app.domains.restaurant.models import Restaurant
from app.domains.restaurant.schemas import (
    ResolveRestaurantRequest,
    RestaurantBrief,
    RestaurantListResponse,
)
from app.domains.restaurant.service import EARTH_M, brief, distance_m
from app.domains.saved.models import SavedRestaurant

NAME_MAX = Restaurant.__table__.c.name.type.length
CATEGORY_MAX = Restaurant.__table__.c.category.type.length
ADDRESS_MAX = Restaurant.__table__.c.address.type.length


def _meters_sql(lat: float, lng: float):
    """service.meters 와 같은 하버사인을 DB 에서"""
    r_lat = func.radians(cast(Restaurant.lat, Float))
    d_lat = r_lat - math.radians(lat)
    d_lng = func.radians(cast(Restaurant.lng, Float)) - math.radians(lng)
    sin_lat, sin_lng = func.sin(d_lat / 2), func.sin(d_lng / 2)
    cos_lat = math.cos(math.radians(lat)) * func.cos(r_lat)
    a = sin_lat * sin_lat + cos_lat * sin_lng * sin_lng
    return 2 * EARTH_M * func.asin(func.sqrt(func.least(a, 1.0)))


async def _saved(session: AsyncSession, user_id: int, ids: list[int]) -> set[int]:
    return set(
        await session.scalars(
            select(SavedRestaurant.restaurant_id).where(
                SavedRestaurant.user_id == user_id,
                SavedRestaurant.restaurant_id.in_(ids),
            )
        )
    )


async def resolve(
    session: AsyncSession, user_id: int, body: ResolveRestaurantRequest
) -> RestaurantBrief:
    """처음이면 카카오에서 다시 찾아 그 값으로 만든다. 바디는 찾는 데만 쓴다"""
    stored = select(Restaurant).where(Restaurant.kakao_place_id == body.kakao_place_id)
    r = await session.scalar(stored)
    if r is None:
        await session.rollback()  # 카카오를 기다리는 동안 DB 커넥션을 잡고 있지 않게
        p = await kakao.find(body.kakao_place_id, body.name, body.lat, body.lng)
        if p is None:
            raise BitokError(
                ErrorCode.NOT_FOUND, "카카오에서 찾을 수 없는 식당입니다.", 404
            )
        await session.execute(
            insert(Restaurant)
            .values(
                kakao_place_id=p.kakao_place_id,
                name=p.name[:NAME_MAX],
                category=p.category and p.category[:CATEGORY_MAX],
                address=p.address and p.address[:ADDRESS_MAX],
                lat=p.lat,
                lng=p.lng,
            )
            .on_conflict_do_nothing(index_elements=["kakao_place_id"])
        )
        r = await session.scalar(stored)
        await session.commit()
    return brief(r, bool(await _saved(session, user_id, [r.id])))


async def _merge(
    session: AsyncSession,
    user_id: int,
    places: list[kakao.Place],
    lat: float | None,
    lng: float | None,
) -> list[RestaurantBrief]:
    """카카오 결과에 우리 식당 정보(id, 핀 숫자, 저장 여부)만 붙인다. 위치와 순서는 카카오 그대로"""
    known = {
        r.kakao_place_id: r
        for r in await session.scalars(
            select(Restaurant).where(
                Restaurant.kakao_place_id.in_([p.kakao_place_id for p in places])
            )
        )
    }
    saved = await _saved(session, user_id, [r.id for r in known.values()])
    near = lat is not None and lng is not None

    items = []
    for p in places:
        r = known.get(p.kakao_place_id)
        items.append(
            RestaurantBrief(
                restaurant_id=r and r.id,
                kakao_place_id=p.kakao_place_id,
                name=p.name[:NAME_MAX],
                category=p.category and p.category[:CATEGORY_MAX],
                address=p.address and p.address[:ADDRESS_MAX],
                lat=p.lat,
                lng=p.lng,
                distance_m=distance_m(lat, lng, p.lat, p.lng) if near else None,
                checked_menu_count=r.checked_menu_count if r else 0,
                is_saved=r is not None and r.id in saved,
            )
        )
    return items


async def nearby(
    session: AsyncSession,
    user_id: int,
    lat: float,
    lng: float,
    radius_m: int,
    limit: int,
) -> RestaurantListResponse:
    """식당 고르기. 카카오 가까운 순 그대로"""
    await session.rollback()  # 카카오를 기다리는 동안 DB 커넥션을 잡고 있지 않게
    places = await kakao.nearby(lat, lng, radius_m, limit)
    return RestaurantListResponse(
        items=await _merge(session, user_id, places, lat, lng)
    )


async def checked(
    session: AsyncSession,
    user_id: int,
    lat: float,
    lng: float,
    radius_m: int,
    limit: int,
) -> RestaurantListResponse:
    """Explore 지도 핀. 메뉴를 확인한 우리 식당만, 가까운 순. 카카오는 안 부른다"""
    # 경계에 걸친 식당도 넣고 정확한 거리로 거른다
    dlat = math.degrees(radius_m / EARTH_M) * 1.01
    dlng = dlat / max(math.cos(math.radians(min(abs(lat) + dlat, 89))), 0.01)
    away = _meters_sql(lat, lng)
    found = list(
        await session.scalars(
            select(Restaurant)
            .where(
                Restaurant.lat.between(lat - dlat, lat + dlat),
                Restaurant.lng.between(lng - dlng, lng + dlng),
                Restaurant.checked_menu_count > 0,
                away <= radius_m,
            )
            .order_by(away, Restaurant.id)
            .limit(limit)
        )
    )
    saved = await _saved(session, user_id, [r.id for r in found])
    return RestaurantListResponse(
        items=[brief(r, r.id in saved, lat, lng) for r in found]
    )


async def search(
    session: AsyncSession,
    user_id: int,
    q: str,
    lat: float | None,
    lng: float | None,
    limit: int,
) -> RestaurantListResponse:
    await session.rollback()  # 카카오를 기다리는 동안 DB 커넥션을 잡고 있지 않게
    places = await kakao.keyword(q, lat, lng, limit)
    return RestaurantListResponse(
        items=await _merge(session, user_id, places, lat, lng)
    )
