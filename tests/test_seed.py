"""성분·음식 사전 시드"""

import json
import zipfile

import pytest
from sqlalchemy import func, select, text

from app.common.models import Dish, DishIngredient, Ingredient, IngredientAlias
from scripts.seed_dictionary import build, read_rows, seed, tags_for


def menu(ko, ingredients=(), allergy=(), roman="", food_type="한식"):
    return {
        "food_type": food_type,
        "ko": ko,
        "en.ROMAN": roman,
        "ingredients.ko": list(ingredients),
        "ingredients.en": [f"{i}-en" for i in ingredients],
        "allergy": list(allergy),
    }


def test_tags_avoid_substring_traps():
    """비건이 쓰는 분류라 오탐·누락이 바로 판정 오류가 된다"""
    assert tags_for("알코올") == set()
    assert tags_for("가오리") == {"fish"}
    assert tags_for("땅콩버터") == set()
    assert tags_for("게맛살") == {"fish"}
    assert tags_for("메추리알") == {"egg"}
    assert tags_for("멸치육수") == {"fish"}
    assert tags_for("홍게") == {"crustacean"}
    assert tags_for("굴소스") == {"shellfish"}
    assert tags_for("보리굴비") == {"fish"}
    assert tags_for("돌멍게") == {"shellfish"}
    assert tags_for("동태내장") == {"fish"}
    assert tags_for("가라아게") == {"meat"}
    assert tags_for("참돔") == {"fish"}
    assert tags_for("돔베고기") == {"meat"}


def test_build_skips_names_too_long_for_columns():
    """하나 때문에 시드 전체가 실패하지 않게 건너뛴다"""
    long = "가" * 130
    plan = build([menu(long, ["김치"])] * 2 + [menu("김치찌개", ["김치"])] * 2)
    assert set(plan.dishes) == {"김치찌개"}
    assert plan.too_long == [long]


def test_build_merges_spacing_and_drops_rare():
    plan = build(
        [
            menu("김치찌개", ["김치"], roman="kimchijjigae"),
            menu("김치찌개", ["김치"], roman="kimchijjigae"),
            menu("김치 찌개", ["김치", "참치"], roman="kimchijjigae"),
            menu("한정메뉴", ["김치"]),
            menu("콜라", ["김치"], food_type="음료/주류"),
        ]
    )
    assert set(plan.dishes) == {"김치찌개"}
    name_ko, name_en, typical = plan.dishes["김치찌개"]
    assert (name_ko, name_en) == ("김치찌개", "Kimchijjigae")
    assert typical == {"김치"}


def test_build_uses_allergy_field():
    """재료 칸은 '해물' 한 단어여도 알레르기 칸에 실제 성분이 있다"""
    rows = [menu("해물파전", ["해물", "파"], ["오징어", "밀가루"])] * 3
    _, _, typical = build(rows).dishes["해물파전"]
    assert typical == {"해산물", "파", "오징어", "연체동물", "밀", "글루텐"}


def test_build_links_sources():
    """검색 목록에 없는 재료도 원재료로 걸린다"""
    plan = build(
        [menu("마파밥", ["두부"])] * 2
        + [menu("연어덮밥", ["연어알"])] * 2
        + [menu("비빔밥", ["참기름"])] * 2
    )
    assert {"대두"} <= plan.dishes["마파밥"][2]
    assert {"연어", "생선알", "생선"} <= plan.dishes["연어덮밥"][2]
    assert {"참깨"} <= plan.dishes["비빔밥"][2]


def test_only_planned_list_is_searchable():
    plan = build([menu("비빔밥", ["양념"])] * 3)
    searchable = {k for k, (_, _, s) in plan.ingredients.items() if s}
    assert len(searchable) == 91
    assert "참깨" in searchable
    assert "양념" in plan.ingredients and "양념" not in searchable


@pytest.mark.asyncio
async def test_seed_is_idempotent(session, tmp_path):
    path = tmp_path / "labels.zip"
    doc = {
        "annotations": [{"menu_information": menu("불고기", ["소고기"], ["쇠고기"])}]
    }
    with zipfile.ZipFile(path, "w") as z:
        for i in range(2):
            z.writestr(f"{i}.json", "\ufeff" + json.dumps(doc, ensure_ascii=False))

    plan = build(read_rows([str(path)]))
    await seed(session, plan)
    await seed(session, plan)

    counts = {
        m.__tablename__: await session.scalar(select(func.count()).select_from(m))
        for m in (Ingredient, IngredientAlias, Dish, DishIngredient)
    }
    assert counts == {
        "ingredients": 91,
        "ingredient_aliases": 15,
        "dishes": 1,
        "dish_ingredients": 1,
    }


@pytest.mark.asyncio
async def test_seed_rerun_applies_changed_rules(session):
    """규칙을 고치고 다시 돌리면 예전 별칭·연결이 남지 않는다"""
    plan = build([menu("불고기", ["소고기", "양파"])] * 3)
    await seed(session, plan)

    session.add(Ingredient(name_ko="옛날항목", name_en="old", searchable=True))
    await session.commit()
    plan.aliases["소고기"] = "양파"
    del plan.aliases["달걀"]
    plan.dishes["불고기"] = ("불고기", "Bulgogi", {"쇠고기"})
    await seed(session, plan)

    rows = await session.execute(
        text(
            "SELECT i.name_ko FROM dish_ingredients di "
            "JOIN ingredients i ON i.id = di.ingredient_id"
        )
    )
    assert [r[0] for r in rows] == ["쇠고기"]
    target = await session.scalar(
        text(
            "SELECT i.name_ko FROM ingredient_aliases a "
            "JOIN ingredients i ON i.id = a.ingredient_id WHERE a.alias = '소고기'"
        )
    )
    assert target == "양파"
    gone = await session.scalar(
        text("SELECT count(*) FROM ingredient_aliases WHERE alias = '달걀'")
    )
    assert gone == 0
    stale = await session.scalar(
        text("SELECT searchable FROM ingredients WHERE name_ko = '옛날항목'")
    )
    assert stale is False


@pytest.mark.asyncio
async def test_generic_ingredient_is_possible(session):
    """육수는 뭘로 만들었는지 몰라서 Avoid 가 아니라 Not Sure 로 가야 한다"""
    await seed(session, build([menu("물냉면", ["육수", "메밀"])] * 3))
    rows = await session.execute(
        text(
            "SELECT i.name_ko, di.likelihood FROM dish_ingredients di "
            "JOIN ingredients i ON i.id = di.ingredient_id"
        )
    )
    assert dict(rows.all()) == {"육수": "possible", "메밀": "typical"}
