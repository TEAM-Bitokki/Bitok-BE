"""성분·음식 사전 시드"""

import json
import zipfile

import pytest
from sqlalchemy import func, select

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
    assert typical == {"해산물", "파", "오징어", "밀"}


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
        "ingredients": 22,
        "ingredient_aliases": 5,
        "dishes": 1,
        "dish_ingredients": 1,
    }
