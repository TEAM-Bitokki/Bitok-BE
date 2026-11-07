"""식당 상세, 식당 메뉴 판정"""

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cursor import decode_cursor, encode_cursor
from app.common.models import Dish, Ingredient
from app.common.schemas import DishBrief, Page
from app.core.config import settings
from app.core.errors import BitokError, ErrorCode
from app.domains.restaurant.menu import menu_detail as build_detail
from app.domains.restaurant.models import Restaurant
from app.domains.restaurant.schemas import (
    MenuDetailResponse,
    PersonalizedMenuItem,
    PersonalizedMenuResponse,
    RestaurantBrief,
    RestaurantDetailResponse,
)
from app.domains.saved.models import SavedRestaurant
from app.domains.scan.judge import Facts, Rules, add_fact, judge, load_rules
from app.domains.scan.models import Analysis, Scan, StaffCheck


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """두 좌표 사이 거리 (하버사인)"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * 6_371_000 * math.asin(math.sqrt(a)))


def brief(
    r: Restaurant,
    is_saved: bool,
    lat: float | None = None,
    lng: float | None = None,
) -> RestaurantBrief:
    return RestaurantBrief(
        restaurant_id=r.id,
        kakao_place_id=r.kakao_place_id,
        name=r.name,
        category=r.category,
        address=r.address,
        lat=float(r.lat),
        lng=float(r.lng),
        distance_m=(
            distance_m(lat, lng, float(r.lat), float(r.lng))
            if lat is not None and lng is not None
            else None
        ),
        checked_menu_count=r.checked_menu_count,
        is_saved=is_saved,
    )


async def _restaurant(session: AsyncSession, restaurant_id: int) -> Restaurant:
    r = await session.get(Restaurant, restaurant_id)
    if r is None:
        raise BitokError(ErrorCode.NOT_FOUND, "없는 식당입니다.", 404)
    return r


async def _rules(session: AsyncSession, user_id: int) -> Rules:
    rules = await load_rules(session, user_id)
    if rules is None:
        raise BitokError(
            ErrorCode.PROFILE_REQUIRED, "식이 프로필을 먼저 저장하세요.", 422
        )
    return rules


async def detail(
    session: AsyncSession,
    user_id: int,
    restaurant_id: int,
    cursor: str | None,
    limit: int,
    lat: float | None,
    lng: float | None,
) -> RestaurantDetailResponse:
    """Checked Menu 는 메뉴마다 한 줄, 최근에 분석한 순"""
    r = await _restaurant(session, restaurant_id)
    saved = await session.scalar(
        select(SavedRestaurant.id).where(
            SavedRestaurant.user_id == user_id,
            SavedRestaurant.restaurant_id == restaurant_id,
        )
    )
    last = func.max(Analysis.created_at)
    query = (
        select(Dish, last)
        .join(Analysis, Analysis.dish_id == Dish.id)
        .join(Scan, Scan.id == Analysis.scan_id)
        .where(Scan.restaurant_id == restaurant_id)
        .group_by(Dish.id)
    )
    if cursor is not None:
        at, dish_id = decode_cursor(cursor)
        query = query.having(tuple_(last, Dish.id) < tuple_(at, dish_id))
    rows = (
        await session.execute(
            query.order_by(last.desc(), Dish.id.desc()).limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return RestaurantDetailResponse(
        restaurant=brief(r, saved is not None, lat, lng),
        checked_menus=[
            DishBrief(dish_id=d.id, name_ko=d.name_ko, name_en=d.name_en)
            for d, _ in rows
        ],
        page=Page(
            next_cursor=(
                encode_cursor(rows[-1][1], rows[-1][0].id) if has_more else None
            ),
            has_more=has_more,
        ),
    )


async def _latest(
    session: AsyncSession, restaurant_id: int, dish_id: int | None = None
) -> list[tuple[Analysis, Dish]]:
    """메뉴마다 가장 최근 분석. 재료는 이걸 쓴다"""
    query = (
        select(Analysis, Dish)
        .join(Dish, Dish.id == Analysis.dish_id)
        .join(Scan, Scan.id == Analysis.scan_id)
        .where(Scan.restaurant_id == restaurant_id)
        .distinct(Analysis.dish_id)
        .order_by(Analysis.dish_id, Analysis.created_at.desc(), Analysis.id.desc())
    )
    if dish_id is not None:
        query = query.where(Analysis.dish_id == dish_id)
    rows = (await session.execute(query)).all()
    return sorted(rows, key=lambda r: (r[0].created_at, r[0].id), reverse=True)


async def load_public_facts(
    session: AsyncSession, restaurant_id: int, dish_ids: list[int]
) -> tuple[dict[int, Facts], dict[int, datetime]]:
    """최근 6개월 모든 사용자의 직원 답. 갈리면 가장 위험한 쪽"""
    since = datetime.now(UTC) - timedelta(days=settings.staff_check_window_days)
    rows = await session.execute(
        select(
            StaffCheck.dish_id,
            StaffCheck.target,
            Ingredient.name_ko,
            StaffCheck.answer,
            StaffCheck.checked_at,
        )
        .join(Ingredient, Ingredient.id == StaffCheck.ingredient_id)
        .where(
            StaffCheck.restaurant_id == restaurant_id,
            StaffCheck.dish_id.in_(dish_ids),
            StaffCheck.checked_at >= since,
        )
    )
    facts, checked = defaultdict(dict), {}
    for dish_id, target, name, answer, at in rows:
        add_fact(facts[dish_id], (target, name), answer)
        checked[dish_id] = max(checked.get(dish_id, at), at)
    return facts, checked


async def for_you(
    session: AsyncSession, user_id: int, restaurant_id: int
) -> PersonalizedMenuResponse:
    await _restaurant(session, restaurant_id)
    rules = await _rules(session, user_id)
    rows = await _latest(session, restaurant_id)
    facts, _ = await load_public_facts(session, restaurant_id, [d.id for _, d in rows])
    return PersonalizedMenuResponse(
        items=[
            PersonalizedMenuItem(
                dish_id=d.id,
                name_ko=d.name_ko,
                name_en=d.name_en,
                analysis_id=a.id,
                verdict=judge(a.matches, rules, facts.get(d.id)),
            )
            for a, d in rows
        ]
    )


async def menu_detail(
    session: AsyncSession, user_id: int, restaurant_id: int, dish_id: int
) -> MenuDetailResponse:
    await _restaurant(session, restaurant_id)
    rules = await _rules(session, user_id)
    rows = await _latest(session, restaurant_id, dish_id)
    if not rows:
        raise BitokError(
            ErrorCode.NOT_FOUND, "이 식당에서 확인한 메뉴가 아닙니다.", 404
        )
    [(a, d)] = rows
    facts, checked = await load_public_facts(session, restaurant_id, [dish_id])
    return build_detail(d, a.matches, rules, facts.get(dish_id), checked.get(dish_id))
