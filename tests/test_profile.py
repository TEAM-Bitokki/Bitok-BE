"""성분 검색 식이 프로필"""

import unicodedata

import pytest
from sqlalchemy import select

from app.common.models import Ingredient, IngredientAlias
from app.domains.profile.models import DietaryProfile

V4 = "550e8400-e29b-41d4-a716-446655440000"
H = {"deviceToken": V4}


@pytest.fixture
async def ids(client, session) -> dict[str, int]:
    client.post("/api/auth/sign-up", json={"device_token": V4})
    rows = {
        "새우": Ingredient(name_ko="새우", name_en="Shrimp"),
        "새우젓": Ingredient(name_ko="새우젓", name_en="Salted Shrimp"),
        "계란": Ingredient(name_ko="계란", name_en="Egg"),
        "양념": Ingredient(name_ko="양념", name_en="Seasoning", searchable=False),
    }
    session.add_all(rows.values())
    await session.flush()
    session.add(IngredientAlias(alias="달걀", ingredient_id=rows["계란"].id))
    await session.commit()
    return {k: v.id for k, v in rows.items()}


def search(client, q: str) -> list[dict]:
    r = client.get("/api/ingredients/search", headers=H, params={"q": q})
    assert r.status_code == 200
    return r.json()["items"]


def test_search_ranks_and_reports_alias(client, ids):
    assert [i["name_ko"] for i in search(client, "새우")] == ["새우", "새우젓"]
    assert search(client, "SHRIMP")[0]["name_ko"] == "새우"
    egg = search(client, "달걀")
    assert [(i["name_ko"], i["matched_alias"]) for i in egg] == [("계란", "달걀")]


def test_search_hides_unlisted_and_wildcards(client, ids):
    assert search(client, "양념") == []
    assert search(client, "%") == []


def test_search_matches_decomposed_hangul(client, ids):
    """맥 등에서 오는 NFD 입력도 같은 글자로 찾는다"""
    nfd = unicodedata.normalize("NFD", "새우")
    assert search(client, nfd)[0]["name_ko"] == "새우"


def test_profile_404_before_save(client, ids):
    assert client.get("/api/profiles/me", headers=H).status_code == 404


def test_put_overwrites_whole_profile(client, ids):
    first = {"allergen_ingredient_ids": [ids["새우"], ids["계란"]], "diet_type": "none"}
    client.put("/api/profiles/me", headers=H, json=first)

    second = {"allergen_ingredient_ids": [ids["새우젓"]], "diet_type": "none"}
    r = client.put("/api/profiles/me", headers=H, json=second)

    assert [a["name_ko"] for a in r.json()["allergens"]] == ["새우젓"]
    assert client.get("/api/auth/me", headers=H).json()["allergen_count"] == 1


def test_skip_still_creates_profile(client, ids):
    """온보딩에서 알레르기를 건너뛰어도 프로필이 있어야 홈으로 간다"""
    body = {"allergen_ingredient_ids": [], "diet_type": "vegan"}
    assert client.put("/api/profiles/me", headers=H, json=body).status_code == 200
    assert client.get("/api/auth/me", headers=H).json()["has_profile"] is True


def test_put_rejects_unlisted_ids(client, ids):
    body = {"allergen_ingredient_ids": [ids["양념"], 9999], "diet_type": "none"}
    r = client.put("/api/profiles/me", headers=H, json=body)

    assert r.status_code == 400
    assert r.json()["error"]["detail"] == {"ingredient_ids": [ids["양념"], 9999]}
    assert client.get("/api/profiles/me", headers=H).status_code == 404


@pytest.mark.parametrize(
    "allergens", [[2**63], [0], list(range(1, 102))], ids=["bigint", "zero", "101개"]
)
def test_put_rejects_out_of_range_ids(client, ids, allergens):
    """DB 에서 터지기 전에 요청 검증에서 막는다"""
    body = {"allergen_ingredient_ids": allergens, "diet_type": "none"}
    r = client.put("/api/profiles/me", headers=H, json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_diet_blocks_by_tag(client, session, ids):
    vegetarian = {
        "allergen_ingredient_ids": [],
        "diet_type": "vegetarian",
        "diet_options": ["eats_egg"],
    }
    client.put("/api/profiles/me", headers=H, json=vegetarian)
    tags = await session.scalar(select(DietaryProfile.blocked_tags))
    assert set(tags) == {"meat", "fish", "shellfish", "crustacean", "dairy"}

    vegan = {**vegetarian, "diet_type": "vegan"}  # 하위 선택은 Vegetarian 만
    r = client.put("/api/profiles/me", headers=H, json=vegan)
    assert r.json()["diet_options"] == []
