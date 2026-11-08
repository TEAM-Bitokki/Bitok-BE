"""식당 상세, 메뉴 판정"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.common.models import Dish, Ingredient
from app.domains.restaurant.menu import common_ingredients
from app.domains.restaurant.models import Restaurant, RestaurantMenuItem
from app.domains.saved.models import SavedRestaurant
from app.domains.scan.models import Scan, StaffCheck
from tests.test_analyze import KIMCHI, H, analyze, info, llm_knows, profile
from tests.test_staff import answer

NAENGMYEON = info("냉면", "Naengmyeon")


async def place(session, *scans) -> Restaurant:
    """식당을 만들고 스캔들을 그 식당에서 찍은 것으로"""
    r = Restaurant(kakao_place_id="1", name="명동교자", lat=37.56, lng=126.98)
    session.add(r)
    await session.flush()
    for scan_id in scans:
        (await session.get(Scan, scan_id)).restaurant_id = r.id
    await session.commit()
    return r


async def another_scan(session, scan_id, restaurant_id=None) -> int:
    first = await session.get(Scan, scan_id)
    s = Scan(user_id=first.user_id, restaurant_id=restaurant_id, image_url="y.jpg")
    session.add(s)
    await session.commit()
    return s.id


def url(r, path=""):
    return f"/api/restaurants/{r.id}{path}"


@pytest.mark.asyncio
async def test_analyze_records_restaurant_menus(client, session, scan, monkeypatch):
    """처음 확인한 메뉴만큼 핀 숫자가 오른다"""
    await profile(client, session)
    llm_knows(monkeypatch, KIMCHI, NAENGMYEON, info("비빔밥", "Bibimbap"))
    r = await place(session, scan)
    analyze(client, scan, "김치찌개", "냉면")
    analyze(client, await another_scan(session, scan, r.id), "김치찌개", "비빔밥")
    analyze(client, await another_scan(session, scan), "김치찌개")  # 식당 없이

    await session.refresh(r)
    items = await session.scalar(select(func.count()).select_from(RestaurantMenuItem))
    assert (r.checked_menu_count, items) == (3, 3)


@pytest.mark.asyncio
async def test_pin_follows_remaining_analyses(client, session, scan, monkeypatch):
    """다시 분석하거나 기록째 탈퇴하면 핀 숫자와 Checked Menu 가 같이 줄어든다"""
    await profile(client, session)
    llm_knows(monkeypatch, KIMCHI, NAENGMYEON)
    r = await place(session, scan)
    analyze(client, scan, "김치찌개")
    analyze(client, scan, "냉면")

    await session.refresh(r)
    menus = client.get(url(r), headers=H).json()["checked_menus"]
    assert r.checked_menu_count == 1
    assert [m["name_ko"] for m in menus] == ["냉면"]

    client.request(
        "DELETE",
        "/api/users/me",
        headers=H,
        json={"agree": True, "delete_saved_menus": True},
    )
    await session.refresh(r)
    assert r.checked_menu_count == 0


@pytest.mark.asyncio
async def test_detail_one_row_per_menu_latest_first(client, session, scan, monkeypatch):
    await profile(client, session)
    llm_knows(monkeypatch, KIMCHI, NAENGMYEON)
    r = await place(session, scan)
    analyze(client, scan, "냉면", "김치찌개")
    analyze(client, await another_scan(session, scan, r.id), "김치찌개")
    user_id = (await session.get(Scan, scan)).user_id
    session.add(SavedRestaurant(user_id=user_id, restaurant_id=r.id))
    await session.commit()

    first = client.get(
        url(r), headers=H, params={"limit": 1, "lat": 37.56, "lng": 126.98}
    )
    body = first.json()
    second = client.get(
        url(r), headers=H, params={"limit": 1, "cursor": body["page"]["next_cursor"]}
    ).json()

    assert first.status_code == 200
    assert body["restaurant"]["is_saved"] is True
    assert body["restaurant"]["distance_m"] == 0
    assert [m["name_ko"] for m in body["checked_menus"]] == ["김치찌개"]
    assert [m["name_ko"] for m in second["checked_menus"]] == ["냉면"]
    assert second["page"] == {"next_cursor": None, "has_more": False}


@pytest.mark.parametrize("days_ago,verdict", [(1, "avoid"), (200, "safe")])
@pytest.mark.asyncio
async def test_public_answers_worst_wins(
    client, session, scan, monkeypatch, days_ago, verdict
):
    """내가 안 들어간다고 들어도 다른 사람이 최근에 들어간다고 들었으면 avoid. 6개월 넘으면 안 본다"""
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    r = await place(session, scan)
    [q] = analyze(client, scan, "김치찌개").json()["questions"]
    answer(client, scan, q, "no")
    dish_id = await session.scalar(select(Dish.id).where(Dish.name_ko == "김치찌개"))
    shrimp_paste = await session.scalar(
        select(Ingredient.id).where(Ingredient.name_ko == "새우젓")
    )
    session.add(
        StaffCheck(
            restaurant_id=r.id,
            dish_id=dish_id,
            ingredient_id=shrimp_paste,
            target="새우젓",
            answer="yes",
            checked_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    await session.commit()

    [item] = client.get(url(r, "/menus/for-you"), headers=H).json()["items"]
    detail = client.get(url(r, f"/menus/{dish_id}"), headers=H).json()

    assert item["verdict"] == verdict
    assert detail["verdict"] == verdict
    assert [(m["ingredient"], m["verdict"]) for m in detail["matches"]] == [
        ("새우", verdict)
    ]
    assert detail["last_checked_at"] is not None


@pytest.mark.parametrize("value,verdict", [("yes", "avoid"), ("unsure", "not_sure")])
@pytest.mark.asyncio
async def test_staff_answer_counts_without_latest_match(
    client, session, scan, monkeypatch, value, verdict
):
    """최신 분석에 새우가 없어도 누가 새우가 들어간다고 들었으면 avoid"""
    await profile(client, session, ["새우"])
    llm_knows(
        monkeypatch, info("제육덮밥", "Jeyukdeopbap", typical=[("돼지고기", "Pork")])
    )
    r = await place(session, scan)
    menu = analyze(client, scan, "제육덮밥").json()["menus"][0]
    shrimp = await session.scalar(
        select(Ingredient.id).where(Ingredient.name_ko == "새우")
    )
    session.add(
        StaffCheck(
            restaurant_id=r.id,
            dish_id=menu["dish_id"],
            ingredient_id=shrimp,
            answer=value,
        )
    )
    await session.commit()

    [item] = client.get(url(r, "/menus/for-you"), headers=H).json()["items"]
    detail = client.get(url(r, f"/menus/{menu['dish_id']}"), headers=H).json()

    assert item["verdict"] == verdict
    assert [(m["ingredient"], m["verdict"]) for m in detail["matches"]] == [
        ("새우", verdict)
    ]


async def public_answer(session, r, dish_id, name, value, target=None):
    """다른 사용자가 남긴 직원 답"""
    i = await session.scalar(select(Ingredient.id).where(Ingredient.name_ko == name))
    session.add(
        StaffCheck(
            restaurant_id=r.id,
            dish_id=dish_id,
            ingredient_id=i,
            target=target,
            answer=value,
        )
    )
    await session.commit()


@pytest.mark.parametrize(
    "dish,name,verdict",
    [
        (KIMCHI, "새우", "safe"),  # 메뉴 전체에 새우가 없다고 들었다
        (NAENGMYEON, "우유", "not_sure"),  # 우유 답만으로 새우를 알 수 없다
    ],
)
@pytest.mark.asyncio
async def test_whole_menu_answers(
    client, session, scan, monkeypatch, dish, name, verdict
):
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, dish)
    r = await place(session, scan)
    menu = analyze(client, scan, dish.name).json()["menus"][0]
    await public_answer(
        session, r, menu["dish_id"], name, "no" if name == "새우" else "unsure"
    )

    [item] = client.get(url(r, "/menus/for-you"), headers=H).json()["items"]
    detail = client.get(url(r, f"/menus/{menu['dish_id']}"), headers=H).json()

    assert (item["verdict"], detail["verdict"]) == (verdict, verdict)


@pytest.mark.asyncio
async def test_menu_detail_common_ingredients(client, session, scan, monkeypatch):
    await profile(client, session)
    llm_knows(monkeypatch, KIMCHI)
    r = await place(session, scan)
    menu = analyze(client, scan, "김치찌개").json()["menus"][0]

    detail = client.get(url(r, f"/menus/{menu['dish_id']}"), headers=H).json()

    assert detail["common_ingredients"] == [
        {"tag_en": "Crustacean Shellfish", "ingredients": ["Salted Shrimp"]},
        {"tag_en": "Meat", "ingredients": ["Pork"]},
        {"tag_en": "Soy", "ingredients": ["Soy"]},  # 시드가 두부를 대두로 넣었다
    ]
    assert detail["last_checked_at"] is None


def test_common_ingredients_keep_narrow_source():
    """밀가루가 밀과 글루텐 둘 다에 걸리면 밀만 보여준다. 육수는 묶지 않는다"""

    def m(name, en, tags=(), via=None):
        return {"ingredient": name, "ingredient_en": en, "tags": list(tags), "via": via}

    matches = [
        m("밀가루", "Flour", via=["밀가루"]),
        m("밀", "Wheat", via=["밀가루"]),
        m("글루텐", "Gluten", via=["밀가루"]),
        m("육수", "Broth", ["meat", "fish"], via=["육수"]),
    ]

    groups = common_ingredients(matches)

    assert [(g.tag_en, g.ingredients) for g in groups] == [("Wheat", ["Flour"])]


@pytest.mark.asyncio
async def test_menu_needs_restaurant_and_menu(client, session, scan, monkeypatch):
    await profile(client, session)
    llm_knows(monkeypatch, KIMCHI)
    r = await place(session)
    menu = analyze(client, scan, "김치찌개").json()["menus"][0]  # 식당 없이

    assert client.get(url(r, f"/menus/{menu['dish_id']}"), headers=H).status_code == 404
    assert client.get("/api/restaurants/999", headers=H).status_code == 404
    assert client.get(f"/api/restaurants/{2**63}", headers=H).status_code == 422


@pytest.mark.asyncio
async def test_analysis_detail_uses_my_answers(client, session, scan, monkeypatch):
    """내 기록의 메뉴 상세는 그 스캔에서 받은 답으로. 남의 분석은 404"""
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    body = analyze(client, scan, "김치찌개").json()
    [q], [menu] = body["questions"], body["menus"]
    answer(client, scan, q, "no")

    r = client.get(f"/api/analyses/{menu['analysis_id']}", headers=H)
    other = {"deviceToken": "7c9e6679-7425-40de-944b-e07fc1f90ae7"}
    client.post("/api/auth/sign-up", json={"device_token": other["deviceToken"]})

    assert r.status_code == 200
    assert r.json()["verdict"] == "safe"
    assert r.json()["dish"]["name_ko"] == "김치찌개"
    assert r.json()["last_checked_at"] is not None
    assert (
        client.get(f"/api/analyses/{menu['analysis_id']}", headers=other).status_code
        == 404
    )
