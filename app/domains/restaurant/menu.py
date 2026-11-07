"""Menu Detail. 식당 상세와 내 기록이 같이 쓴다"""

from collections import defaultdict
from datetime import datetime

from app.common.dictionary import GENERIC, SEARCHABLE, with_sources
from app.common.models import Dish
from app.common.schemas import DishBrief
from app.domains.restaurant.schemas import CommonIngredientGroup, MenuDetailResponse
from app.domains.scan.judge import TAG_EN, Facts, Rules, flagged, judge

ALLERGENS = {ko for ko, _, _ in SEARCHABLE}


def common_ingredients(matches: list[dict]) -> list[CommonIngredientGroup]:
    """프로필과 상관없이 분류별로 묶는다. 새우젓은 Crustacean Shellfish, 두부는 Soybeans"""
    en = {m["ingredient"]: m["ingredient_en"] for m in matches}

    def members(m: dict) -> set[str]:
        return {en.get(v, v) for v in m.get("via") or [m["ingredient"]]}

    groups = defaultdict(set)
    for m in matches:
        if m["ingredient"] in GENERIC:  # 육수가 고기인지 생선인지는 모른다
            continue
        for tag in m["tags"]:
            if tag in TAG_EN:
                groups[TAG_EN[tag]] |= members(m)

    plain = {
        m["ingredient"]: m
        for m in matches
        if not m["tags"] and m["ingredient"] in ALLERGENS
    }
    for name, m in plain.items():
        broader = any(  # 밀가루가 밀과 글루텐에 다 걸리면 밀만
            name in with_sources({other}) - {other} and members(m) <= members(o)
            for other, o in plain.items()
            if other != name
        )
        if not broader:
            groups[m["ingredient_en"]] |= members(m)

    return [
        CommonIngredientGroup(tag_en=label, ingredients=sorted(names))
        for label, names in sorted(groups.items())
    ]


def menu_detail(
    dish: Dish,
    matches: list[dict],
    rules: Rules,
    facts: Facts | None,
    last_checked_at: datetime | None,
) -> MenuDetailResponse:
    return MenuDetailResponse(
        dish=DishBrief(dish_id=dish.id, name_ko=dish.name_ko, name_en=dish.name_en),
        verdict=judge(matches, rules, facts),
        matches=flagged(matches, rules, facts),
        common_ingredients=common_ingredients(matches),
        last_checked_at=last_checked_at.isoformat() if last_checked_at else None,
    )
