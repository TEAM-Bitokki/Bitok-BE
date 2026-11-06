"""메뉴 분석, 직원 질문"""

import pytest
import pytest_asyncio
from google.genai import errors
from sqlalchemy import func, select

from app.common.models import Dish, Ingredient, dish_key
from app.core import llm
from app.core.config import settings
from app.domains.scan import dish
from app.domains.scan.dish import DishInfo, DishInfos, IngredientName
from app.domains.scan.judge import Rules, judge
from app.domains.scan.models import Analysis, Scan, StaffQuestion
from scripts.seed_dictionary import build, seed
from tests.test_seed import menu

V4 = "550e8400-e29b-41d4-a716-446655440000"
H = {"deviceToken": V4}


def info(name, en, typical=(), possible=(), unknown=False):
    return DishInfo(
        name=name,
        name_en=en,
        unknown=unknown,
        typical=[IngredientName(name_ko=k, name_en=e) for k, e in typical],
        possible=[IngredientName(name_ko=k, name_en=e) for k, e in possible],
    )


KIMCHI = info("김치찌개", "Kimchijjigae", possible=[("새우젓", "Salted Shrimp")])


def llm_knows(monkeypatch, *infos):
    calls = []
    known = {i.name: i for i in infos}

    async def fake(names):
        calls.append(names)
        return [known[n] for n in names]

    monkeypatch.setattr(dish, "_describe", fake)
    return calls


@pytest_asyncio.fixture
async def scan(client, session, monkeypatch):
    """김치찌개가 시드에 있다. 돼지고기, 두부가 보통 들어간다"""
    monkeypatch.setattr(settings, "mock_llm", False)
    rows = [menu("김치찌개", ["돼지고기", "두부"], roman="kimchijjigae")] * 2
    await seed(session, build(rows))
    user_id = client.post("/api/auth/sign-up", json={"device_token": V4}).json()[
        "user_id"
    ]
    s = Scan(user_id=user_id, image_url="x.jpg")
    session.add(s)
    await session.commit()
    return s.id


async def profile(client, session, allergens=(), diet="none"):
    ids = list(
        await session.scalars(
            select(Ingredient.id).where(Ingredient.name_ko.in_(allergens))
        )
    )
    r = client.put(
        "/api/profiles/me",
        headers=H,
        json={"allergen_ingredient_ids": ids, "diet_type": diet},
    )
    assert r.status_code == 200


def analyze(client, scan_id, *names, selected=None):
    items = [{"temp_id": f"i{n}", "raw_name": name} for n, name in enumerate(names, 1)]
    return client.post(
        f"/api/scans/{scan_id}/analyze",
        headers=H,
        json={
            "items": items,
            "selected_temp_ids": selected or [i["temp_id"] for i in items],
        },
    )


@pytest.mark.asyncio
async def test_seeded_dish_asks_hidden_ingredient(client, session, scan, monkeypatch):
    """시드에 없는 숨은 재료는 모델이 채우고, 그건 직원에게 묻는다"""
    await profile(client, session, ["새우"])
    calls = llm_knows(monkeypatch, KIMCHI)

    r = analyze(client, scan, "김치찌개")

    assert r.status_code == 200
    [m] = r.json()["menus"]
    assert m["verdict"] == "not_sure"
    assert [(x["ingredient"], x["status"], x["tag"]) for x in m["matches"]] == [
        ("새우", "allergy", None)
    ]
    [q] = r.json()["questions"]
    assert q["question_ko"] == "이 김치찌개에 새우젓이 들어가나요?"
    assert q["question_en"] == "Does this Kimchijjigae contain salted shrimp?"
    assert (q["seq"], q["total"]) == (1, 1)

    analyze(client, scan, "김치찌개")
    assert calls == [["김치찌개"]]  # 두 번째는 저장된 재료를 쓴다


@pytest.mark.asyncio
async def test_typical_hit_avoids_without_question(client, session, scan, monkeypatch):
    """돼지고기는 거의 확실히 들어가니 묻지 않는다"""
    await profile(client, session, diet="vegan")
    llm_knows(monkeypatch, KIMCHI)

    r = analyze(client, scan, "김치찌개")

    [m] = r.json()["menus"]
    assert m["verdict"] == "avoid"
    assert ("돼지고기", "avoid", "meat", "typical") in [
        (x["ingredient"], x["status"], x["tag"], x["likelihood"]) for x in m["matches"]
    ]
    assert r.json()["questions"] == []


@pytest.mark.asyncio
async def test_new_dish_follows_sources(client, session, scan, monkeypatch):
    """처음 보는 메뉴도 시드처럼 원재료를 따라간다. 두부 -> 대두"""
    await profile(client, session, ["대두"])
    llm_knows(
        monkeypatch,
        info("마파두부밥", "Mapadububap", typical=[("두부", "Tofu"), ("밥", "Rice")]),
    )

    r = analyze(client, scan, "마파두부밥")

    [m] = r.json()["menus"]
    assert (m["name_ko"], m["name_en"], m["verdict"]) == (
        "마파두부밥",
        "Mapadububap",
        "avoid",
    )
    assert [x["ingredient"] for x in m["matches"]] == ["대두"]
    new = await session.scalar(select(Ingredient).where(Ingredient.name_ko == "밥"))
    assert new.searchable is False


@pytest.mark.asyncio
async def test_unknown_dish_asks_profile(client, session, scan, monkeypatch):
    """재료를 모르면 지어내지 않고, 고른 알레르기와 식단을 그대로 묻는다"""
    await profile(client, session, ["새우"], diet="pescatarian")
    llm_knows(monkeypatch, info("사장님특제", "Sajangnimteukje", unknown=True))

    r = analyze(client, scan, "사장님특제")

    assert r.json()["menus"][0]["verdict"] == "not_sure"
    assert [q["question_ko"] for q in r.json()["questions"]] == [
        "이 사장님특제에 새우가 들어가나요?",
        "이 사장님특제에 고기가 들어가나요?",
    ]


@pytest.mark.asyncio
async def test_questions_capped_allergy_first(client, session, scan, monkeypatch):
    await profile(client, session, ["새우"], diet="vegetarian")
    monkeypatch.setattr(settings, "max_staff_questions", 1)
    llm_knows(
        monkeypatch,
        info("잔치국수", "Janchiguksu", possible=[("멸치액젓", "Fish Sauce")]),
        info("계란밥", "Gyeranbap", possible=[("새우젓", "Salted Shrimp")]),
    )

    r = analyze(client, scan, "잔치국수", "계란밥")

    assert [q["question_ko"] for q in r.json()["questions"]] == [
        "이 계란밥에 새우젓이 들어가나요?"
    ]


@pytest.mark.asyncio
async def test_same_menu_twice_and_size_marks(client, session, scan, monkeypatch):
    await profile(client, session)
    calls = llm_knows(monkeypatch, KIMCHI)

    r = analyze(client, scan, "김치찌개 (대)", "김치 찌개")

    assert [m["name_ko"] for m in r.json()["menus"]] == ["김치찌개"]
    assert r.json()["menus"][0]["verdict"] == "safe"
    assert calls == [["김치찌개"]]


def test_size_marks_only():
    """오징어순대의 '대' 는 크기가 아니다"""
    assert dish_key("김치찌개 (대)") == dish_key("김치찌개(대)") == "김치찌개"
    assert dish_key("짜장면 곱빼기") == "짜장면"
    assert dish_key("삼겹살2인분") == "삼겹살"
    assert dish_key("오징어순대") == "오징어순대"
    assert dish_key("돈까스(치즈)") == "돈까스(치즈)"


@pytest.mark.asyncio
async def test_reanalyze_replaces(client, session, scan, monkeypatch):
    """뒤로 가서 다시 고르면 이전 분석은 지운다"""
    await profile(client, session, ["새우"])
    llm_knows(
        monkeypatch, KIMCHI, info("공기밥", "Gonggibap", typical=[("쌀", "Rice")])
    )

    analyze(client, scan, "김치찌개", "공기밥")
    r = analyze(client, scan, "김치찌개", "공기밥", selected=["i2"])

    assert r.status_code == 200
    assert await session.scalar(select(func.count()).select_from(Analysis)) == 1
    assert await session.scalar(select(func.count()).select_from(StaffQuestion)) == 0


@pytest.mark.asyncio
async def test_llm_failure_saves_nothing(client, session, scan, monkeypatch):
    await profile(client, session)

    async def down(model, system, contents, schema):
        raise errors.ServerError(503, {"error": {"message": "high demand"}})

    monkeypatch.setattr(llm, "_call", down)
    r = analyze(client, scan, "마파두부밥")

    assert r.status_code == 502
    assert (
        await session.scalar(select(Dish).where(Dish.name_ko == "마파두부밥")) is None
    )


@pytest.mark.asyncio
async def test_llm_wrong_menu_uses_fallback(client, session, scan, monkeypatch):
    """받은 메뉴와 다른 메뉴로 답하면 예비 모델에 다시 묻는다"""
    await profile(client, session)
    models = []

    async def mixed(model, system, contents, schema):
        models.append(model)
        name = "엉뚱한메뉴" if model == settings.gemini_model else "마파두부밥"
        return DishInfos(dishes=[info(name, "Mapadububap")])

    monkeypatch.setattr(llm, "_call", mixed)
    r = analyze(client, scan, "마파두부밥")

    assert r.status_code == 200
    assert models == [settings.gemini_model, settings.gemini_fallback_model]


@pytest.mark.parametrize(
    "body",
    [
        {"items": [{"temp_id": "i1", "raw_name": "국밥"}], "selected_temp_ids": ["i9"]},
        {"items": [{"temp_id": "i1", "raw_name": "  "}], "selected_temp_ids": ["i1"]},
        {"items": [{"temp_id": "i1", "raw_name": "국밥"}], "selected_temp_ids": []},
    ],
    ids=["없는 항목", "빈 이름", "고른 게 없음"],
)
@pytest.mark.asyncio
async def test_analyze_rejects_bad_body(client, session, scan, body):
    await profile(client, session)
    r = client.post(f"/api/scans/{scan}/analyze", headers=H, json=body)
    assert r.status_code in (400, 422)
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_other_users_scan_404(client, session, scan):
    other = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"
    client.post("/api/auth/sign-up", json={"device_token": other})
    r = client.post(
        f"/api/scans/{scan}/analyze",
        headers={"deviceToken": other},
        json={
            "items": [{"temp_id": "i1", "raw_name": "국밥"}],
            "selected_temp_ids": ["i1"],
        },
    )
    assert r.status_code == 404


def test_judge_rechecks_with_new_profile():
    """저장한 메뉴 목록은 지금 프로필로 다시 판정한다"""
    shrimp = {
        "ingredient_id": 7,
        "ingredient": "새우",
        "tags": ["crustacean"],
        "likelihood": "possible",
    }
    assert judge([shrimp], Rules(frozenset(), frozenset())) == "safe"
    assert judge([shrimp], Rules(frozenset({7}), frozenset())) == "not_sure"
    assert judge([], Rules(frozenset({7}), frozenset())) == "not_sure"
    assert judge([shrimp], Rules(frozenset(), frozenset({"meat"}))) == "safe"


def test_generic_matches_allergy_by_tag():
    """해산물은 새우일 수 있다. 다른 생선끼리는 분류가 같아도 걸지 않는다"""
    shrimp = Rules(frozenset({7}), frozenset(), frozenset({"crustacean"}))
    seafood = {
        "ingredient_id": 9,
        "ingredient": "해산물",
        "tags": ["crustacean", "fish", "shellfish"],
        "likelihood": "possible",
    }
    crab = {"ingredient_id": 8, "ingredient": "게", "tags": ["crustacean"]}
    assert judge([seafood], shrimp) == "not_sure"
    assert judge([crab | {"likelihood": "typical"}], shrimp) == "safe"


@pytest.mark.asyncio
async def test_broth_asks_what_it_is_made_of(client, session, scan, monkeypatch):
    await profile(client, session, ["생선"])
    llm_knows(
        monkeypatch,
        info("냉면", "Naengmyeon", possible=[("육수", "Broth")]),
    )

    r = analyze(client, scan, "냉면")

    [m] = r.json()["menus"]
    assert m["verdict"] == "not_sure"
    assert [x["ingredient"] for x in m["matches"]] == ["육수"]
    [q] = r.json()["questions"]
    assert q["question_ko"] == "이 냉면의 육수에 생선이 들어가나요?"
    assert q["question_en"] == "Does the broth in this Naengmyeon contain fish?"


@pytest.mark.parametrize(
    "diet,questions",
    [
        (
            "vegan",
            [
                "이 냉면의 육수에 고기가 들어가나요?",
                "이 냉면의 육수에 해산물이 들어가나요?",
            ],
        ),
        ("pescatarian", ["이 냉면의 육수에 고기가 들어가나요?"]),
    ],
)
@pytest.mark.asyncio
async def test_broth_asks_by_diet(client, session, scan, monkeypatch, diet, questions):
    """육수가 들었는지가 아니라 뭘로 만들었는지를 묻는다. 생선 육수는 페스코에게 괜찮다"""
    await profile(client, session, diet=diet)
    llm_knows(monkeypatch, info("냉면", "Naengmyeon", possible=[("육수", "Broth")]))

    r = analyze(client, scan, "냉면")

    assert [q["question_ko"] for q in r.json()["questions"]] == questions


@pytest.mark.asyncio
async def test_seafood_asks_plainly_for_vegan(client, session, scan, monkeypatch):
    await profile(client, session, diet="vegan")
    llm_knows(
        monkeypatch, info("해물파전", "Haemulpajeon", possible=[("해산물", "Seafood")])
    )

    r = analyze(client, scan, "해물파전")

    assert [q["question_ko"] for q in r.json()["questions"]] == [
        "이 해물파전에 해산물이 들어가나요?"
    ]
