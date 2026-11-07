"""저장한 메뉴 목록 조회 API"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.common.models import Dish, Ingredient
from app.core.security import hash_token
from app.domains.auth.models import User
from app.domains.check.models import MyCheck
from app.domains.profile.models import DietaryProfile, ProfileAllergen
from app.domains.restaurant.models import Restaurant
from app.domains.scan.models import Analysis, Scan

V4 = "550e8400-e29b-41d4-a716-446655440000"
OTHER_V4 = "550e8400-e29b-41d4-a716-446655440001"
H = {"deviceToken": V4}


async def _user_with_profile(client, session, token: str = V4) -> int:
    user_id = client.post(
        "/api/auth/sign-up", json={"device_token": token}
    ).json()["user_id"]
    session.add(DietaryProfile(user_id=user_id, diet_type="none", blocked_tags={}))
    await session.commit()
    return user_id


async def _add_check(
    session,
    user_id: int,
    key: str,
    saved_at: datetime,
    restaurant: Restaurant | None,
    matches: list[dict] | None = None,
) -> MyCheck:
    dish = Dish(canonical_name=key, name_ko=key, name_en=f"{key} EN")
    session.add(dish)
    await session.flush()
    scan = Scan(
        user_id=user_id,
        restaurant_id=restaurant.id if restaurant else None,
        image_url=f"{key}.jpg",
    )
    session.add(scan)
    await session.flush()
    analysis = Analysis(
        scan_id=scan.id,
        dish_id=dish.id,
        verdict="safe",
        matches=matches or [],
    )
    session.add(analysis)
    await session.flush()
    check = MyCheck(
        user_id=user_id,
        analysis_id=analysis.id,
        restaurant_id=restaurant.id if restaurant else None,
        saved_at=saved_at,
    )
    session.add(check)
    await session.commit()
    return check


@pytest.mark.asyncio
async def test_list_my_checks_returns_empty_page(client, session):
    await _user_with_profile(client, session)

    r = client.get("/api/my-checks", headers=H)

    assert r.status_code == 200
    assert r.json() == {
        "items": [],
        "page": {"next_cursor": None, "has_more": False},
    }


@pytest.mark.asyncio
async def test_list_my_checks_returns_card_fields(client, session):
    user_id = await _user_with_profile(client, session)
    restaurant = Restaurant(
        kakao_place_id="place-1",
        name="Myeongdong Kyoja",
        lat=37.5,
        lng=127.0,
    )
    session.add(restaurant)
    await session.commit()
    saved_at = datetime(2026, 11, 8, 11, 30, tzinfo=UTC)
    check = await _add_check(session, user_id, "비빔밥", saved_at, restaurant)
    analysis = await session.get(Analysis, check.analysis_id)

    r = client.get("/api/my-checks", headers=H)

    assert r.status_code == 200
    [item] = r.json()["items"]
    assert item == {
        "dish_id": analysis.dish_id,
        "name_ko": "비빔밥",
        "name_en": "비빔밥 EN",
        "my_check_id": check.id,
        "analysis_id": analysis.id,
        "restaurant_id": restaurant.id,
        "restaurant_name": "Myeongdong Kyoja",
        "verdict": "safe",
        "saved_at": saved_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_list_my_checks_is_newest_first_and_private(client, session):
    user_id = await _user_with_profile(client, session)
    other = User(device_token=hash_token(OTHER_V4))
    session.add(other)
    await session.commit()
    now = datetime.now(UTC)
    older = await _add_check(session, user_id, "이전메뉴", now - timedelta(days=1), None)
    newer = await _add_check(session, user_id, "최근메뉴", now, None)
    await _add_check(
        session,
        other.id,
        "다른사용자메뉴",
        now + timedelta(days=1),
        None,
    )

    r = client.get("/api/my-checks", headers=H)

    assert r.status_code == 200
    assert [item["my_check_id"] for item in r.json()["items"]] == [newer.id, older.id]


@pytest.mark.asyncio
async def test_list_my_checks_recalculates_verdict(client, session):
    user_id = await _user_with_profile(client, session)
    ingredient = Ingredient(
        name_ko="새우",
        name_en="Shrimp",
        allergen_tags=["crustacean"],
    )
    session.add(ingredient)
    await session.flush()
    profile = await session.scalar(
        select(DietaryProfile).where(DietaryProfile.user_id == user_id)
    )
    session.add(ProfileAllergen(profile_id=profile.id, ingredient_id=ingredient.id))
    await session.commit()
    matches = [
        {
            "ingredient_id": ingredient.id,
            "ingredient": "새우",
            "ingredient_en": "Shrimp",
            "tags": ["crustacean"],
            "likelihood": "possible",
            "source": "curated",
            "via": ["새우"],
        }
    ]
    await _add_check(session, user_id, "새우메뉴", datetime.now(UTC), None, matches)

    r = client.get("/api/my-checks", headers=H)

    assert r.status_code == 200
    assert r.json()["items"][0]["verdict"] == "not_sure"


@pytest.mark.asyncio
async def test_list_my_checks_uses_cursor(client, session):
    user_id = await _user_with_profile(client, session)
    now = datetime.now(UTC)
    checks = [
        await _add_check(session, user_id, f"메뉴{i}", now, None)
        for i in range(3)
    ]

    first = client.get("/api/my-checks?limit=2", headers=H).json()
    second = client.get(
        "/api/my-checks",
        headers=H,
        params={"limit": 2, "cursor": first["page"]["next_cursor"]},
    ).json()

    assert [item["my_check_id"] for item in first["items"]] == [
        checks[2].id,
        checks[1].id,
    ]
    assert first["page"]["has_more"] is True
    assert first["page"]["next_cursor"] is not None
    assert [item["my_check_id"] for item in second["items"]] == [checks[0].id]
    assert second["page"] == {"next_cursor": None, "has_more": False}


def test_list_my_checks_rejects_invalid_cursor(client):
    client.post("/api/auth/sign-up", json={"device_token": V4})

    r = client.get("/api/my-checks?cursor=not-a-cursor", headers=H)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_save_my_checks_saves_all_analyses(client, session):
    user_id = await _user_with_profile(client, session)
    restaurant = Restaurant(
        kakao_place_id="save-place",
        name="저장 식당",
        lat=37.5,
        lng=127.0,
    )
    session.add(restaurant)
    await session.flush()
    scan = Scan(user_id=user_id, restaurant_id=restaurant.id, image_url="menu.jpg")
    session.add(scan)
    dishes = [
        Dish(canonical_name=f"save-{i}", name_ko=f"메뉴{i}", name_en=f"Menu {i}")
        for i in range(3)
    ]
    session.add_all(dishes)
    await session.flush()
    analyses = [
        Analysis(scan_id=scan.id, dish_id=dish.id, verdict="safe", matches=[])
        for dish in dishes
    ]
    session.add_all(analyses)
    await session.commit()

    r = client.post("/api/my-checks", headers=H, json={"scan_id": scan.id})

    assert r.status_code == 201
    checks = (
        await session.scalars(
            select(MyCheck).where(MyCheck.user_id == user_id).order_by(MyCheck.id)
        )
    ).all()
    assert [check.analysis_id for check in checks] == [item.id for item in analyses]
    assert {check.restaurant_id for check in checks} == {restaurant.id}


@pytest.mark.asyncio
async def test_save_my_checks_is_idempotent(client, session):
    user_id = await _user_with_profile(client, session)
    check = await _add_check(
        session, user_id, "이미저장", datetime.now(UTC), None
    )
    analysis = await session.get(Analysis, check.analysis_id)

    first = client.post(
        "/api/my-checks", headers=H, json={"scan_id": analysis.scan_id}
    )
    second = client.post(
        "/api/my-checks", headers=H, json={"scan_id": analysis.scan_id}
    )

    assert first.status_code == 201
    assert second.status_code == 201
    saved = (
        await session.scalars(
            select(MyCheck).where(MyCheck.analysis_id == analysis.id)
        )
    ).all()
    assert len(saved) == 1


@pytest.mark.asyncio
async def test_save_my_checks_rejects_another_users_scan(client, session):
    await _user_with_profile(client, session)
    other = User(device_token=hash_token(OTHER_V4))
    session.add(other)
    await session.commit()
    other_check = await _add_check(
        session, other.id, "남의메뉴", datetime.now(UTC), None
    )
    analysis = await session.get(Analysis, other_check.analysis_id)

    r = client.post(
        "/api/my-checks", headers=H, json={"scan_id": analysis.scan_id}
    )

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_save_my_checks_rejects_scan_without_analysis(client, session):
    user_id = await _user_with_profile(client, session)
    scan = Scan(user_id=user_id, image_url="not-analyzed.jpg")
    session.add(scan)
    await session.commit()

    r = client.post("/api/my-checks", headers=H, json={"scan_id": scan.id})

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
