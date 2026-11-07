"""메뉴 분석, 직원 질문"""

import pytest
from google.genai import errors
from sqlalchemy import func, select

from app.common.dictionary import with_sources
from app.common.models import (
    Dish,
    DishAlias,
    DishIngredient,
    Ingredient,
    dish_key,
)
from app.core import llm
from app.core.config import settings
from app.domains.profile.models import DietaryProfile, ProfileAllergen
from app.domains.scan import dish
from app.domains.scan.dish import DishInfo, DishInfos, IngredientName
from app.domains.scan.judge import Rules, judge
from app.domains.scan.models import Analysis, StaffQuestion

V4 = "550e8400-e29b-41d4-a716-446655440000"
H = {"deviceToken": V4}


def _name(ko, en, contains=()):
    return IngredientName(name_ko=ko, name_en=en, contains=list(contains))


def info(name, en, typical=(), possible=(), unknown=False):
    return DishInfo(
        name=name,
        name_en=en,
        unknown=unknown,
        typical=[_name(*t) for t in typical],
        possible=[_name(*t) for t in possible],
    )


KIMCHI = info("김치찌개", "Kimchijjigae", possible=[("새우젓", "Salted Shrimp")])


def llm_knows(monkeypatch, *infos):
    calls = []
    known = {i.name: i for i in infos}

    async def fake(names):
        calls.append(names)
        return {n: known[n] for n in names}

    monkeypatch.setattr(dish, "_describe", fake)
    return calls


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


@pytest.mark.parametrize(
    "body,code",
    [
        (
            {
                "items": [{"temp_id": "i1", "raw_name": "국밥"}],
                "selected_temp_ids": ["i9"],
            },
            400,
        ),
        (
            {
                "items": [{"temp_id": "i1", "raw_name": "  "}],
                "selected_temp_ids": ["i1"],
            },
            422,
        ),
        (
            {"items": [{"temp_id": "i1", "raw_name": "국밥"}], "selected_temp_ids": []},
            422,
        ),
    ],
    ids=["없는 항목", "빈 이름", "고른 게 없음"],
)
@pytest.mark.asyncio
async def test_analyze_rejects_bad_body(client, session, scan, body, code):
    await profile(client, session)
    r = client.post(f"/api/scans/{scan}/analyze", headers=H, json=body)
    assert r.status_code == code
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
async def test_llm_may_fix_spacing(client, session, scan, monkeypatch):
    """모델이 띄어쓰기를 고쳐서 답해도 같은 메뉴로 본다"""
    await profile(client, session)

    async def spaced(model, system, contents, schema):
        return DishInfos(dishes=[info("마파두부밥", "Mapadububap")])

    monkeypatch.setattr(llm, "_call", spaced)
    r = analyze(client, scan, "마파 두부밥")

    assert r.status_code == 200
    assert r.json()["menus"][0]["name_en"] == "Mapadububap"


@pytest.mark.parametrize("first", ["possible", "typical"])
@pytest.mark.asyncio
async def test_alias_keeps_typical(client, session, scan, monkeypatch, first):
    """달걀과 계란은 같은 재료다. 어느 쪽이 먼저 와도 typical 이 남는다"""
    await profile(client, session, ["계란"])
    egg = [("달걀", "Egg")], [("계란", "Egg")]
    possible, typical = egg if first == "possible" else egg[::-1]
    llm_knows(
        monkeypatch,
        info("계란말이", "Gyeranmari", typical=typical, possible=possible),
    )

    r = analyze(client, scan, "계란말이")

    assert r.json()["menus"][0]["verdict"] == "avoid"


@pytest.mark.asyncio
async def test_dish_aliases_merge_descriptions(client, session, scan, monkeypatch):
    """김치찌게(오타)도 김치찌개다. 두 설명 중 한쪽에만 있는 재료도 남는다"""
    kimchi = await session.scalar(select(Dish).where(Dish.name_ko == "김치찌개"))
    session.add(DishAlias(dish_id=kimchi.id, alias="김치찌게"))
    await session.commit()
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI, info("김치찌게", "Kimchijjige"))

    r = analyze(client, scan, "김치찌개", "김치찌게")

    [m] = r.json()["menus"]
    assert m["verdict"] == "not_sure"
    assert [q["question_ko"] for q in r.json()["questions"]] == [
        "이 김치찌개에 새우젓이 들어가나요?"
    ]


def test_duplicate_temp_ids_rejected(client, scan):
    r = client.post(
        f"/api/scans/{scan}/analyze",
        headers=H,
        json={
            "items": [
                {"temp_id": "i1", "raw_name": "새우볶음밥"},
                {"temp_id": "i1", "raw_name": "공기밥"},
            ],
            "selected_temp_ids": ["i1"],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_long_ingredient_list_is_not_cut(client, session, scan, monkeypatch):
    """재료가 많아도 자르지 않는다. 잘린 뒤쪽 재료로 safe 가 나오면 안 된다"""
    await profile(client, session, ["새우"])
    many = [(f"재료{n}", f"Thing {n}") for n in range(40)]
    llm_knows(
        monkeypatch,
        info(
            "모둠정식", "Modumjeongsik", possible=many + [("새우젓", "Salted Shrimp")]
        ),
    )

    r = analyze(client, scan, "모둠정식")

    assert r.json()["menus"][0]["verdict"] == "not_sure"


@pytest.mark.asyncio
async def test_broth_may_hide_shrimp(client, session, scan, monkeypatch):
    """새우 육수도 있다"""
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, info("짬뽕", "Jjamppong", possible=[("육수", "Broth")]))

    r = analyze(client, scan, "짬뽕")

    [q] = r.json()["questions"]
    assert q["question_ko"] == "이 짬뽕의 육수에 새우가 들어가나요?"
    assert q["question_en"] == "Does the broth in this Jjamppong contain shrimp?"


@pytest.mark.asyncio
async def test_too_long_ingredient_uses_fallback(client, session, scan, monkeypatch):
    """저장할 수 없는 재료 이름이 오면 버리지 않고 예비 모델에 다시 묻는다"""
    await profile(client, session, ["새우"])
    models = []

    async def long(model, system, contents, schema):
        models.append(model)
        name = "새우" * 50 if model == settings.gemini_model else "새우젓"
        return DishInfos(
            dishes=[info("볶음밥", "Bokkeumbap", possible=[(name, "Shrimp")])]
        )

    monkeypatch.setattr(llm, "_call", long)
    r = analyze(client, scan, "볶음밥")

    assert models == [settings.gemini_model, settings.gemini_fallback_model]
    assert r.json()["menus"][0]["verdict"] == "not_sure"


@pytest.mark.asyncio
async def test_profile_changed_while_waiting(
    client, session, scan, monkeypatch, other_session
):
    """모델을 기다리는 동안 새우 알레르기를 추가하면 새 프로필로 판정한다"""
    await profile(client, session)
    shrimp = await session.scalar(
        select(Ingredient.id).where(Ingredient.name_ko == "새우")
    )

    async def slow(names):
        async with other_session() as other:
            p = await other.scalar(select(DietaryProfile))
            other.add(ProfileAllergen(profile_id=p.id, ingredient_id=shrimp))
            await other.commit()
        return {"김치찌개": KIMCHI}

    monkeypatch.setattr(dish, "_describe", slow)
    r = analyze(client, scan, "김치찌개")

    assert r.json()["menus"][0]["verdict"] == "not_sure"


@pytest.mark.asyncio
async def test_model_sources_catch_meat(client, session, scan, monkeypatch):
    """이름 규칙에 없는 재료도 모델이 알려준 원재료로 걸린다. 그 메뉴에만 붙는다"""
    await profile(client, session, diet="vegetarian")
    llm_knows(
        monkeypatch,
        info(
            "크리스피롤",
            "Keuriseupirol",
            typical=[("크리스피스트립", "Crispy Strip", ["닭고기", "밀"])],
        ),
        info("스트립볼", "Seuteuripbol", typical=[("크리스피스트립", "Crispy Strip")]),
    )

    first = analyze(client, scan, "크리스피롤").json()["menus"][0]
    second = analyze(client, scan, "스트립볼").json()["menus"][0]

    assert first["verdict"] == "avoid"
    assert second["verdict"] == "safe"
    assert ("닭고기", "meat") in [(m["ingredient"], m["tag"]) for m in first["matches"]]


@pytest.mark.parametrize(
    "allergens,diet,verdict",
    [([], "vegan", "safe"), (["아몬드"], "none", "avoid"), (["우유"], "none", "safe")],
    ids=["비건", "아몬드 알레르기", "우유 알레르기"],
)
@pytest.mark.asyncio
async def test_almond_milk_is_almond(
    client, session, scan, monkeypatch, allergens, diet, verdict
):
    await profile(client, session, allergens, diet)
    llm_knows(
        monkeypatch,
        info(
            "아몬드라떼",
            "Amondeuratte",
            typical=[("아몬드우유", "Almond Milk", ["아몬드"])],
        ),
    )

    r = analyze(client, scan, "아몬드라떼")

    assert r.json()["menus"][0]["verdict"] == verdict


@pytest.mark.parametrize(
    "name,has,lacks",
    [
        ("치킨텐더", {"닭고기"}, set()),
        ("돈까스", {"돼지고기", "밀"}, set()),
        ("아몬드우유", {"아몬드"}, {"우유", "유제품"}),
        ("귀리우유", {"귀리"}, {"우유"}),
        ("두유", {"대두"}, set()),
        ("콩고기", {"대두"}, set()),
        ("치킨무", set(), {"닭고기"}),
        ("우동면", {"밀"}, set()),
        ("만두피", {"밀"}, set()),
        ("당면", set(), {"밀"}),
    ],
)
def test_name_rules_for_common_cases(name, has, lacks):
    """모델이 원재료를 빠뜨려도 흔한 이름은 규칙으로 잡는다"""
    found = with_sources({name})
    assert has <= found
    assert not lacks & found


@pytest.mark.asyncio
async def test_sources_from_every_menu_are_kept(client, session, scan, monkeypatch):
    """같은 롤반죽이라도 계란은 에그롤에만 붙는다. 다음 요청의 다른 메뉴에도 번지지 않는다"""
    await profile(client, session, ["계란"])
    llm_knows(
        monkeypatch,
        info("에그롤", "Egeurol", typical=[("롤반죽", "Roll Dough", ["계란"])]),
        info("야채롤", "Yachaerol", typical=[("롤반죽", "Roll Dough")]),
        info("김밥롤", "Gimbapnol", typical=[("롤반죽", "Roll Dough")]),
    )

    r = analyze(client, scan, "에그롤", "야채롤")
    later = analyze(client, scan, "김밥롤")

    assert [m["verdict"] for m in r.json()["menus"]] == ["avoid", "safe"]
    assert later.json()["menus"][0]["verdict"] == "safe"


@pytest.mark.asyncio
async def test_broth_contents_stay_with_the_dish(client, session, scan, monkeypatch):
    """육수에 밀이 든다고 하면 그 메뉴에만 밀을 붙인다"""
    await profile(client, session, ["밀"])
    llm_knows(
        monkeypatch,
        info("짬뽕", "Jjamppong", possible=[("육수", "Broth", ["밀"])]),
        info("냉면", "Naengmyeon", possible=[("육수", "Broth")]),
    )

    jjamppong = analyze(client, scan, "짬뽕").json()
    naengmyeon = analyze(client, scan, "냉면").json()

    assert jjamppong["menus"][0]["verdict"] == "not_sure"
    assert [q["question_ko"] for q in jjamppong["questions"]] == [
        "이 짬뽕의 육수에 밀이 들어가나요?"
    ]
    assert naengmyeon["menus"][0]["verdict"] == "safe"


@pytest.mark.parametrize("source,verdict", [("llm", "avoid"), ("curated", "not_sure")])
@pytest.mark.asyncio
async def test_typical_upgrades_earlier_model_answer(
    client, session, scan, monkeypatch, source, verdict
):
    """다른 요청이 먼저 possible 로 넣었어도 typical 이 오면 올린다. 검수 데이터는 그대로"""
    await profile(client, session, ["새우"])
    d = Dish(
        canonical_name="새우볶음밥", name_ko="새우볶음밥", name_en="Saeubokkeumbap"
    )
    session.add(d)
    await session.flush()
    shrimp = await session.scalar(
        select(Ingredient.id).where(Ingredient.name_ko == "새우")
    )
    session.add(
        DishIngredient(
            dish_id=d.id, ingredient_id=shrimp, likelihood="possible", source=source
        )
    )
    await session.commit()
    llm_knows(
        monkeypatch,
        info("새우볶음밥", "Saeubokkeumbap", typical=[("새우", "Shrimp")]),
    )

    r = analyze(client, scan, "새우볶음밥")

    assert r.json()["menus"][0]["verdict"] == verdict


@pytest.mark.asyncio
async def test_unlisted_source_is_kept(client, session, scan, monkeypatch):
    """목록에 없는 원재료(닭가슴살)도 버리지 않는다. 이름 규칙이 닭고기를 찾는다"""
    await profile(client, session, diet="vegetarian")
    llm_knows(
        monkeypatch,
        info(
            "스트립랩",
            "Seuteuripraep",
            typical=[("크리스피스트립", "Crispy Strip", ["닭가슴살"])],
        ),
    )

    r = analyze(client, scan, "스트립랩")

    assert r.json()["menus"][0]["verdict"] == "avoid"


@pytest.mark.asyncio
async def test_broth_asks_about_its_sources_too(client, session, scan, monkeypatch):
    """비건인데 육수에 우유가 든다고 하면 유제품도 묻는다"""
    await profile(client, session, diet="vegan")
    llm_knows(
        monkeypatch,
        info("크림우동", "Keurimudong", possible=[("육수", "Broth", ["우유"])]),
    )

    r = analyze(client, scan, "크림우동")

    assert "이 크림우동의 육수에 유제품이 들어가나요?" in [
        q["question_ko"] for q in r.json()["questions"]
    ]
