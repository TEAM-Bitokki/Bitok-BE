"""저장한 메뉴"""

import base64
import json
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
from tests.test_analyze import KIMCHI, analyze, info, llm_knows, profile
from tests.test_staff import answer

V4 = "550e8400-e29b-41d4-a716-446655440000"
OTHER_V4 = "550e8400-e29b-41d4-a716-446655440001"
H = {"deviceToken": V4}


async def _user_with_profile(client, session, token: str = V4) -> int:
    user_id = client.post("/api/auth/sign-up", json={"device_token": token}).json()[
        "user_id"
    ]
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
    older = await _add_check(
        session, user_id, "이전메뉴", now - timedelta(days=1), None
    )
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
        await _add_check(session, user_id, f"메뉴{i}", now, None) for i in range(3)
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


def _cursor(my_check_id) -> str:
    raw = json.dumps({"at": "2026-11-08T11:30:00+00:00", "id": my_check_id})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


@pytest.mark.parametrize("cursor", ["not-a-cursor", _cursor(0), _cursor(2**63)])
def test_list_my_checks_rejects_invalid_cursor(client, cursor):
    client.post("/api/auth/sign-up", json={"device_token": V4})

    r = client.get("/api/my-checks", headers=H, params={"cursor": cursor})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def save(client, scan_id, headers=H):
    return client.post("/api/my-checks", headers=headers, json={"scan_id": scan_id})


@pytest.mark.asyncio
async def test_save_keeps_every_menu_once(client, session, scan, monkeypatch):
    """스캔의 메뉴를 전부 저장한다. 두 번 눌러도 한 번만"""
    place = Restaurant(kakao_place_id="1", name="명동교자", lat=37.56, lng=126.98)
    session.add(place)
    await session.flush()
    (await session.get(Scan, scan)).restaurant_id = place.id
    await session.commit()
    await profile(client, session)
    llm_knows(monkeypatch, KIMCHI, info("냉면", "Naengmyeon"))
    analyze(client, scan, "김치찌개", "냉면")

    assert save(client, scan).status_code == 204
    first = client.get("/api/my-checks", headers=H).json()["items"]
    assert save(client, scan).status_code == 204
    items = client.get("/api/my-checks", headers=H).json()["items"]

    assert items == first  # 저장 id, 시각 그대로
    assert sorted(i["name_ko"] for i in items) == ["김치찌개", "냉면"]
    assert {i["restaurant_name"] for i in items} == {"명동교자"}


@pytest.mark.asyncio
async def test_list_uses_each_scans_answers(client, session, scan, monkeypatch):
    """같은 메뉴를 두 번 저장해도 스캔마다 그때 직원 답으로 판정한다"""
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    again = Scan(user_id=(await session.get(Scan, scan)).user_id, image_url="y.jpg")
    session.add(again)
    await session.commit()
    for scan_id, value in ((scan, "no"), (again.id, "yes")):
        [q] = analyze(client, scan_id, "김치찌개").json()["questions"]
        answer(client, scan_id, q, value)
        save(client, scan_id)

    items = client.get("/api/my-checks", headers=H).json()["items"]
    assert [i["verdict"] for i in items] == ["avoid", "safe"]  # 최신순


@pytest.mark.asyncio
async def test_save_needs_my_analyzed_scan(client, session, scan, monkeypatch):
    await profile(client, session)
    assert save(client, scan).status_code == 404  # 분석 전

    llm_knows(monkeypatch, KIMCHI)
    analyze(client, scan, "김치찌개")
    other = {"deviceToken": OTHER_V4}
    client.post("/api/auth/sign-up", json={"device_token": OTHER_V4})
    assert save(client, scan, other).status_code == 404
    assert save(client, scan + 1).status_code == 404


@pytest.mark.asyncio
async def test_saved_scan_cannot_be_analyzed_again(client, session, scan, monkeypatch):
    """다시 분석하면 저장한 메뉴가 지워지므로 막는다"""
    await profile(client, session)
    llm_knows(monkeypatch, KIMCHI)
    analyze(client, scan, "김치찌개")
    save(client, scan)

    r = analyze(client, scan, "김치찌개")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ALREADY_EXISTS"
    assert len(client.get("/api/my-checks", headers=H).json()["items"]) == 1
