"""AI Hub 관광 음식메뉴판 라벨로 성분, 음식 사전 채우기"""

import asyncio
import json
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dictionary import (
    GENERIC,
    SEARCHABLE,
    SYNONYMS,
    tags_for,
    with_sources,
)
from app.common.models import (
    Dish,
    DishIngredient,
    Ingredient,
    IngredientAlias,
    dish_key,
    menu_name,
)
from app.core.db import SessionMaker
from scripts.retag_ingredients import retag

MIN_INGREDIENT = 3  # 이보다 적게 나온 재료는 버린다
MIN_DISH = 2  # 이보다 적게 나온 메뉴는 런타임 LLM 몫
TYPICAL_RATIO = 0.5  # 같은 메뉴 중 이 비율 이상에 나온 재료만 typical
INGREDIENT_MAX = Ingredient.__table__.c.name_ko.type.length
DISH_MAX = Dish.__table__.c.name_ko.type.length
PARAMS_MAX = 32767


def roman(text: str) -> str:
    """kimchijjigae -> Kimchijjigae"""
    text = text.strip()
    return text[:1].upper() + text[1:]


@dataclass
class Plan:
    ingredients: dict[str, tuple[str, set[str], bool]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    dishes: dict[str, tuple[str, str, set[str]]] = field(default_factory=dict)
    too_long: list[str] = field(default_factory=list)


def read_rows(paths: list[str]) -> list[dict]:
    rows = []
    for path in paths:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith(".json"):
                    doc = json.loads(z.read(name).decode("utf-8-sig"))
                    rows += [a["menu_information"] for a in doc.get("annotations", [])]
    return rows


def build(rows: list[dict]) -> Plan:
    plan = Plan()
    alias_of = {}
    for ko, en, aliases in SEARCHABLE:
        plan.ingredients[ko] = (en, tags_for(ko), True)
        for alias in aliases:
            alias_of[alias] = ko
    alias_of |= SYNONYMS

    def resolve(name: str) -> str:
        name = name.strip()
        return alias_of.get(name, name)

    count, en_of = Counter(), defaultdict(Counter)
    for row in rows:
        ko, en = row["ingredients.ko"], row["ingredients.en"]
        for i, name in enumerate(ko):
            name = resolve(name)
            count[name] += 1
            if len(en) == len(ko):
                en_of[name][en[i]] += 1

    for name, n in count.items():
        if name in plan.ingredients or n < MIN_INGREDIENT or not en_of[name]:
            continue
        en = en_of[name].most_common(1)[0][0]
        if max(len(name), len(en)) > INGREDIENT_MAX:
            plan.too_long.append(name)
            continue
        plan.ingredients[name] = (en, tags_for(name), False)
    plan.aliases = {a: ko for a, ko in alias_of.items() if ko in plan.ingredients}

    groups = defaultdict(list)
    for row in rows:
        if row["food_type"] == "음료/주류" or not (
            row["ingredients.ko"] or row["allergy"]
        ):
            continue
        groups[dish_key(row["ko"])].append(row)

    for key, group in groups.items():
        if len(group) < MIN_DISH or not key:
            continue
        seen = Counter()
        for row in group:
            names = {resolve(i) for i in row["ingredients.ko"] + row["allergy"]}
            seen.update(n for n in with_sources(names) if n in plan.ingredients)
        typical = {n for n, c in seen.items() if c / len(group) >= TYPICAL_RATIO}
        name_ko = Counter(menu_name(r["ko"]) for r in group).most_common(1)[0][0]
        name_en = roman(Counter(r["en.ROMAN"] for r in group).most_common(1)[0][0])
        if max(len(key), len(name_ko), len(name_en)) > DISH_MAX:
            plan.too_long.append(key)
            continue
        plan.dishes[key] = (name_ko, name_en, typical)
    return plan


def _batches(rows: list[dict]) -> list[list[dict]]:
    """asyncpg 는 한 쿼리에 값을 32767 개까지만 받는다"""
    if not rows:
        return []
    size = PARAMS_MAX // len(rows[0])
    return [rows[i : i + size] for i in range(0, len(rows), size)]


async def _merge_alias_rows(
    session: AsyncSession, aliases: dict[str, str], ids: dict[str, int]
) -> None:
    """별칭과 이름이 같은 재료가 따로 있으면 (분석이 먼저 만든 소고기) 연결을 원래 재료로 옮긴다"""
    for alias, ko in aliases.items():
        old = ids.get(alias)
        if old is None or old == ids[ko]:
            continue
        await session.execute(
            text("""
                INSERT INTO dish_ingredients (dish_id, ingredient_id, likelihood, source)
                SELECT dish_id, :new, likelihood, source
                FROM dish_ingredients WHERE ingredient_id = :old
                ON CONFLICT ON CONSTRAINT uq_dish_ingredient DO UPDATE
                SET likelihood = CASE
                    WHEN excluded.likelihood = 'typical' THEN 'typical'
                    ELSE dish_ingredients.likelihood END
                """),
            {"new": ids[ko], "old": old},
        )
        await session.execute(
            delete(DishIngredient).where(DishIngredient.ingredient_id == old)
        )


async def seed(session: AsyncSession, plan: Plan) -> dict[str, int]:
    """다시 돌리면 성분, 별칭, public_data 연결을 지금 규칙대로 맞춘다"""
    rows = [
        {"name_ko": ko, "name_en": en, "allergen_tags": sorted(t), "searchable": s}
        for ko, (en, t, s) in plan.ingredients.items()
    ]
    for chunk in _batches(rows):
        stmt = insert(Ingredient).values(chunk)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["name_ko"],
                set_={
                    "name_en": stmt.excluded.name_en,
                    "allergen_tags": stmt.excluded.allergen_tags,
                    "searchable": stmt.excluded.searchable,
                },
            )
        )
    await retag(session)  # 분석이 예전 규칙으로 만든 재료도
    listed = [ko for ko, (_, _, s) in plan.ingredients.items() if s]
    await session.execute(
        update(Ingredient)
        .where(Ingredient.name_ko.not_in(listed))
        .values(searchable=False)
    )
    ids = dict((await session.execute(select(Ingredient.name_ko, Ingredient.id))).all())

    await session.execute(
        delete(IngredientAlias).where(IngredientAlias.alias.not_in(list(plan.aliases)))
    )
    aliases = [{"alias": a, "ingredient_id": ids[ko]} for a, ko in plan.aliases.items()]
    for chunk in _batches(aliases):
        stmt = insert(IngredientAlias).values(chunk)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["alias"],
                set_={"ingredient_id": stmt.excluded.ingredient_id},
            )
        )
    await _merge_alias_rows(session, plan.aliases, ids)

    dishes = [
        {"canonical_name": k, "name_ko": ko, "name_en": en}
        for k, (ko, en, _) in plan.dishes.items()
    ]
    for chunk in _batches(dishes):
        await session.execute(
            insert(Dish)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["canonical_name"])
        )
    dish_ids = dict((await session.execute(select(Dish.canonical_name, Dish.id))).all())
    # public_data 는 시드만 쓴다. 규칙에서 빠진 메뉴의 연결도 같이 지운다
    await session.execute(
        delete(DishIngredient).where(DishIngredient.source == "public_data")
    )

    links = [
        {
            "dish_id": dish_ids[k],
            "ingredient_id": ids[n],
            "likelihood": "possible" if n in GENERIC else "typical",
            "source": "public_data",
        }
        for k, (_, _, names) in plan.dishes.items()
        for n in names
    ]
    for chunk in _batches(links):
        await session.execute(
            insert(DishIngredient)
            .values(chunk)
            .on_conflict_do_nothing(constraint="uq_dish_ingredient")
        )
    await session.commit()

    return {
        "ingredients": len(plan.ingredients),
        "aliases": len(plan.aliases),
        "dishes": len(plan.dishes),
        "dish_ingredients": len(links),
        "too_long": plan.too_long,
    }


async def main(paths: list[str]) -> None:
    plan = build(read_rows(paths))
    async with SessionMaker() as session:
        print(await seed(session, plan))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('python -m scripts.seed_dictionary "VL_01. 서울.zip" [...]')
    asyncio.run(main(sys.argv[1:]))
