"""성분·음식 사전 시드"""

import json
import zipfile

import pytest
from sqlalchemy import delete, func, select, text

from app.common.models import (
    Dish,
    DishIngredient,
    DishIngredientSource,
    Ingredient,
    IngredientAlias,
)
from scripts.retag_ingredients import retag
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


@pytest.mark.asyncio
async def test_seed_handles_many_dishes(session):
    """메뉴가 1만 개를 넘어도 한 쿼리 값 한도(32767)에 안 걸린다"""
    plan = build([])
    plan.dishes = {f"메뉴{i}": (f"메뉴{i}", f"Menu{i}", {"쌀"}) for i in range(11_000)}

    await seed(session, plan)

    assert await session.scalar(select(func.count()).select_from(Dish)) == 11_000
    assert (
        await session.scalar(select(func.count()).select_from(DishIngredient)) == 11_000
    )


def test_build_drops_size_marks():
    """김치찌개 (대) 와 김치찌개는 같은 메뉴다. 분석과 같은 키를 쓴다"""
    plan = build(
        [
            menu("김치찌개 (대)", ["김치"]),
            menu("김치찌개", ["김치"]),
            menu("오징어순대", ["오징어"]),
        ]
        * 2
    )
    assert set(plan.dishes) == {"김치찌개", "오징어순대"}
    assert plan.dishes["김치찌개"][0] == "김치찌개"


@pytest.mark.asyncio
async def test_seed_moves_links_from_alias_rows(session):
    """분석이 먼저 만든 '소고기' 재료가 있으면 시드가 쇠고기로 옮긴다"""
    beef = Ingredient(name_ko="소고기", name_en="Beef", searchable=False)
    dish = Dish(canonical_name="불고기", name_ko="불고기", name_en="Bulgogi")
    session.add_all([beef, dish])
    await session.flush()
    session.add(
        DishIngredient(
            dish_id=dish.id, ingredient_id=beef.id, likelihood="typical", source="llm"
        )
    )
    await session.commit()

    await seed(session, build([]))

    rows = await session.execute(
        select(Ingredient.name_ko)
        .join(DishIngredient, DishIngredient.ingredient_id == Ingredient.id)
        .where(DishIngredient.dish_id == dish.id)
    )
    assert rows.scalars().all() == ["쇠고기"]


@pytest.mark.asyncio
async def test_retag_alone_fixes_old_tags(session):
    """시드 없이도 분류만 다시 맞출 수 있다. 배포할 때 이것만 돌린다"""
    session.add_all(
        [
            Ingredient(name_ko="육수", name_en="Broth", allergen_tags=["fish", "meat"]),
            Ingredient(name_ko="두유", name_en="Soy Milk", allergen_tags=["dairy"]),
        ]
    )
    await session.commit()

    assert await retag(session) == 2
    await session.commit()
    assert await retag(session) == 0

    rows = dict(
        (
            await session.execute(select(Ingredient.name_ko, Ingredient.allergen_tags))
        ).all()
    )
    assert rows == {"육수": ["crustacean", "fish", "meat", "shellfish"], "두유": []}


@pytest.mark.parametrize(
    "kept,alias,result",
    [
        (("possible", "curated"), ("typical", "llm"), ("possible", "curated")),
        (("typical", "public_data"), ("typical", "llm"), ("typical", "llm")),
    ],
    ids=["검수 데이터가 이긴다", "모델 답은 다음 시드에도 남는다"],
)
@pytest.mark.asyncio
async def test_alias_merge_keeps_trusted_link(session, kept, alias, result):
    beef = Ingredient(name_ko="소고기", name_en="Beef", searchable=False)
    dish = Dish(canonical_name="불고기", name_ko="불고기", name_en="Bulgogi")
    session.add_all([beef, dish])
    await seed(session, build([]))
    real = await session.scalar(
        select(Ingredient.id).where(Ingredient.name_ko == "쇠고기")
    )
    soy = await session.scalar(
        select(Ingredient.id).where(Ingredient.name_ko == "대두")
    )
    await session.execute(delete(DishIngredient))
    session.add_all(
        [
            DishIngredient(
                dish_id=dish.id, ingredient_id=real, likelihood=kept[0], source=kept[1]
            ),
            DishIngredient(
                dish_id=dish.id,
                ingredient_id=beef.id,
                likelihood=alias[0],
                source=alias[1],
            ),
            DishIngredientSource(dish_id=dish.id, ingredient_id=beef.id, source_id=soy),
        ]
    )
    await session.commit()

    await seed(session, build([]))

    row = (
        await session.execute(
            select(DishIngredient.likelihood, DishIngredient.source).where(
                DishIngredient.dish_id == dish.id
            )
        )
    ).all()
    assert row == [result]
    sources = await session.execute(
        select(DishIngredientSource.ingredient_id, DishIngredientSource.source_id)
    )
    assert sources.all() == [(real, soy)]


@pytest.mark.asyncio
async def test_partial_seed_keeps_other_menus(session):
    """다른 지역 라벨만 시드해도 먼저 시드한 메뉴의 재료는 남는다"""
    await seed(session, build([menu("김치찌개", ["두부"])] * 2))
    await seed(session, build([menu("냉면", ["메밀"])] * 2))

    rows = await session.execute(
        select(Dish.name_ko)
        .join(DishIngredient, DishIngredient.dish_id == Dish.id)
        .distinct()
    )
    assert set(rows.scalars()) == {"김치찌개", "냉면"}
