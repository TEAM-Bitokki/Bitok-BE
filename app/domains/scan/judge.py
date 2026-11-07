"""판정. 모델을 부르지 않는다

analyses.matches, 지금 프로필, 직원 답만 있으면 언제든 다시 계산할 수 있다.
"""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dictionary import GENERIC
from app.common.models import Ingredient
from app.common.schemas import Likelihood, MatchStatus, StaffAnswer, Verdict
from app.domains.profile.models import DietaryProfile, ProfileAllergen
from app.domains.restaurant.schemas import MenuMatch
from app.domains.scan.models import StaffQuestion

TAG_EN = {
    "meat": "Meat",
    "fish": "Fish",
    "shellfish": "Shellfish",
    "crustacean": "Crustacean Shellfish",
    "dairy": "Dairy",
    "egg": "Egg",
    "honey": "Honey",
}

# 식단을 물을 때 쓰는 말
ASK_FOR_TAG = {
    "meat": ("고기", "Meat"),
    "fish": ("해산물", "Seafood"),
    "shellfish": ("해산물", "Seafood"),
    "crustacean": ("해산물", "Seafood"),
    "dairy": ("유제품", "Dairy"),
    "egg": ("계란", "Egg"),
    "honey": ("꿀", "Honey"),
}

# (물은 재료, 들었는지 물은 것) -> 답. 물은 재료가 None 이면 메뉴 전체에 물은 것
Facts = dict[tuple[str | None, str], str]


@dataclass(frozen=True)
class Rules:
    """프로필에서 판정에 쓰는 것"""

    allergen_ids: frozenset[int]
    blocked_tags: frozenset[str]
    allergen_tags: frozenset[str] = frozenset()  # 고른 알레르기의 분류. 육수에 쓴다
    allergens: tuple[tuple[str, frozenset[str]], ...] = ()  # (이름, 분류)


async def load_rules(session: AsyncSession, user_id: int) -> Rules | None:
    profile = await session.scalar(
        select(DietaryProfile).where(DietaryProfile.user_id == user_id)
    )
    if profile is None:
        return None
    rows = (
        await session.execute(
            select(Ingredient.id, Ingredient.name_ko, Ingredient.allergen_tags)
            .join(ProfileAllergen, ProfileAllergen.ingredient_id == Ingredient.id)
            .where(ProfileAllergen.profile_id == profile.id)
        )
    ).all()
    return Rules(
        frozenset(i for i, _, _ in rows),
        frozenset(profile.blocked_tags),
        frozenset(t for _, _, tags in rows for t in tags),
        tuple(sorted((name, frozenset(tags)) for _, name, tags in rows)),
    )


async def load_facts(session: AsyncSession, scan_id: int) -> dict[int, Facts]:
    """메뉴 -> 이 스캔에서 직원이 답한 것"""
    rows = await session.execute(
        select(
            StaffQuestion.dish_id,
            StaffQuestion.target,
            Ingredient.name_ko,
            StaffQuestion.answer,
        )
        .join(Ingredient, Ingredient.id == StaffQuestion.ingredient_id)
        .where(StaffQuestion.scan_id == scan_id, StaffQuestion.answer.is_not(None))
    )
    facts = defaultdict(dict)
    for dish_id, target, name, answer in rows:
        facts[dish_id][(target, name)] = answer
    return facts


def status(match: dict, rules: Rules) -> MatchStatus:
    if match["ingredient_id"] in rules.allergen_ids:
        return MatchStatus.ALLERGY
    if match["ingredient"] in GENERIC and rules.allergen_tags & set(match["tags"]):
        return MatchStatus.ALLERGY  # 생선 알레르기면 육수도 확인해야 한다
    if rules.blocked_tags & set(match["tags"]):
        return MatchStatus.AVOID
    return MatchStatus.ALLOWED


def _words(tags: set[str]) -> set[str]:
    return {ASK_FOR_TAG[t][0] for t in tags if t in ASK_FOR_TAG}


def _reasons(match: dict, rules: Rules) -> set[str]:
    """육수가 왜 걸렸나. 직원에게 물은 말과 같은 이름으로"""
    tags = set(match["tags"])
    allergy = {name for name, t in rules.allergens if t & tags}
    return allergy | _words(rules.blocked_tags & tags)


def _answered(facts: Facts, keys: list[tuple[str | None, str]]) -> Verdict | None:
    """하나라도 들어간다면 avoid, 전부 안 들어간다면 safe, 아니면 모름"""
    answers = [facts.get(k) for k in keys]
    if StaffAnswer.YES in answers:
        return Verdict.AVOID
    if keys and all(a == StaffAnswer.NO for a in answers):
        return Verdict.SAFE
    return None


def match_verdict(match: dict, rules: Rules, facts: Facts) -> Verdict:
    """걸린 재료 하나. 가게마다 다른 재료는 직원이 안 들어간다고 해야 safe"""
    if match["likelihood"] == Likelihood.TYPICAL:
        return Verdict.AVOID
    verdicts = []
    for row in match.get("via") or [match["ingredient"]]:
        keys = []
        if row in GENERIC:
            if match["ingredient"] == row:
                keys = [(row, n) for n in sorted(_reasons(match, rules))]
            elif status(match, rules) == MatchStatus.ALLERGY:
                keys = [(row, match["ingredient"])]
            else:  # 비건에게 육수의 우유는 "육수에 유제품" 으로 물었다
                words = _words(rules.blocked_tags & set(match["tags"]))
                keys = [(row, w) for w in sorted(words)] or [(row, match["ingredient"])]
        if StaffAnswer.YES in {facts.get(k) for k in keys}:
            v = Verdict.AVOID  # 해산물이 없다고 해도 새우가 있다고 했으면 avoid
        else:
            v = _answered(facts, [(row, row)]) or _answered(facts, keys)
        verdicts.append(v)
    if Verdict.AVOID in verdicts:
        return Verdict.AVOID
    if verdicts and all(v == Verdict.SAFE for v in verdicts):
        return Verdict.SAFE
    return Verdict.NOT_SURE


def judge(matches: list[dict], rules: Rules, facts: Facts | None = None) -> Verdict:
    """facts 가 없으면 직원 확인 전 판정. 재료를 하나도 모르면 not_sure"""
    if not (rules.allergen_ids or rules.blocked_tags):
        return Verdict.SAFE
    facts = facts or {}
    if not matches:  # 고른 알레르기와 식단을 그대로 물었다
        names = {name for name, _ in rules.allergens} | _words(rules.blocked_tags)
        return _answered(facts, [(None, n) for n in sorted(names)]) or (
            Verdict.NOT_SURE
        )
    verdicts = {
        match_verdict(m, rules, facts)
        for m in matches
        if status(m, rules) != MatchStatus.ALLOWED
    }
    for v in (Verdict.AVOID, Verdict.NOT_SURE):
        if v in verdicts:
            return v
    return Verdict.SAFE


def flagged(
    matches: list[dict], rules: Rules, facts: Facts | None = None
) -> list[MenuMatch]:
    """걸린 성분만"""
    result = []
    for m in matches:
        s = status(m, rules)
        if s == MatchStatus.ALLOWED:
            continue
        tag = (
            None
            if s == MatchStatus.ALLERGY
            else min(rules.blocked_tags & set(m["tags"]))
        )
        result.append(
            MenuMatch(
                ingredient=m["ingredient"],
                ingredient_en=m["ingredient_en"],
                tag=tag,
                tag_en=TAG_EN.get(tag) if tag else None,
                status=s,
                likelihood=m["likelihood"],
                verdict=match_verdict(m, rules, facts or {}),
            )
        )
    return result
