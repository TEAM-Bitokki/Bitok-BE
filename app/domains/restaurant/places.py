"""지도. 식당 등록, 주변 식당, 이름 검색"""

import math
from collections.abc import Sequence

from sqlalchemy import select
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
from app.domains.restaurant.service import EARTH_M, brief, distance_m, meters
from app.domains.saved.models import SavedRestaurant

NAME_MAX = Restaurant.__table__.c.name.type.length
CATEGORY_MAX = Restaurant.__table__.c.category.type.length
ADDRESS_MAX = Restaurant.__table__.c.address.type.length


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
    extra: Sequence[Restaurant] = (),
) -> list[RestaurantBrief]:
    """카카오 결과에 우리 식당 정보(id, 핀 숫자, 저장 여부)를 붙인다"""
    known = {
        r.kakao_place_id: r
        for r in await session.scalars(
            select(Restaurant).where(
                Restaurant.kakao_place_id.in_([p.kakao_place_id for p in places])
            )
        )
    }
    known |= {r.kakao_place_id: r for r in extra}
    saved = await _saved(session, user_id, [r.id for r in known.values()])
    near = lat is not None and lng is not None

    items = []
    for p in places:
        r = known.get(p.kakao_place_id)
        if r is not None:
            items.append(brief(r, r.id in saved, lat, lng))
            continue
        items.append(
            RestaurantBrief(
                kakao_place_id=p.kakao_place_id,
                name=p.name[:NAME_MAX],
                category=p.category and p.category[:CATEGORY_MAX],
                address=p.address and p.address[:ADDRESS_MAX],
                lat=p.lat,
                lng=p.lng,
                distance_m=distance_m(lat, lng, p.lat, p.lng) if near else None,
            )
        )
    ids = {p.kakao_place_id for p in places}
    items += [
        brief(r, r.id in saved, lat, lng) for r in extra if r.kakao_place_id not in ids
    ]
    return items


async def nearby(
    session: AsyncSession,
    user_id: int,
    lat: float,
    lng: float,
    radius_m: int,
    limit: int,
) -> RestaurantListResponse:
    """메뉴를 확인한 식당은 카카오 상위 결과에 없어도 반경 안이면 넣는다"""
    await session.rollback()  # 카카오를 기다리는 동안 DB 커넥션을 잡고 있지 않게
    places = await kakao.nearby(lat, lng, radius_m, limit)
    # 경계에 걸친 식당도 넣고 아래서 정확한 거리로 거른다
    dlat = math.degrees(radius_m / EARTH_M) * 1.01
    dlng = dlat / max(math.cos(math.radians(min(abs(lat) + dlat, 89))), 0.01)
    checked = await session.scalars(
        select(Restaurant).where(
            Restaurant.lat.between(lat - dlat, lat + dlat),
            Restaurant.lng.between(lng - dlng, lng + dlng),
            Restaurant.checked_menu_count > 0,
        )
    )
    items = await _merge(session, user_id, places, lat, lng, list(checked))
    inside = [i for i in items if meters(lat, lng, i.lat, i.lng) <= radius_m]
    inside.sort(key=lambda i: meters(lat, lng, i.lat, i.lng))
    return RestaurantListResponse(items=inside)


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
    items = await _merge(session, user_id, places, lat, lng)
    if lat is not None and lng is not None:  # 저장된 좌표가 카카오와 다를 수 있다
        items.sort(key=lambda i: meters(lat, lng, i.lat, i.lng))
    return RestaurantListResponse(items=items)
