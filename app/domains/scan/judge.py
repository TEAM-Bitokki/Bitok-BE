"""판정. 모델을 부르지 않는다

analyses.matches 와 지금 프로필만 있으면 언제든 다시 계산할 수 있다.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dictionary import GENERIC
from app.common.models import Ingredient
from app.common.schemas import Likelihood, MatchStatus, Verdict
from app.domains.profile.models import DietaryProfile, ProfileAllergen
from app.domains.restaurant.schemas import MenuMatch

TAG_EN = {
    "meat": "Meat",
    "fish": "Fish",
    "shellfish": "Shellfish",
    "crustacean": "Crustacean Shellfish",
    "dairy": "Dairy",
    "egg": "Egg",
    "honey": "Honey",
}


@dataclass(frozen=True)
class Rules:
    """프로필에서 판정에 쓰는 것"""

    allergen_ids: frozenset[int]
    blocked_tags: frozenset[str]
    allergen_tags: frozenset[str] = (
        frozenset()
    )  # 고른 알레르기의 분류. 육수, 해산물에 쓴다


async def load_rules(session: AsyncSession, user_id: int) -> Rules | None:
    profile = await session.scalar(
        select(DietaryProfile).where(DietaryProfile.user_id == user_id)
    )
    if profile is None:
        return None
    rows = (
        await session.execute(
            select(Ingredient.id, Ingredient.allergen_tags)
            .join(ProfileAllergen, ProfileAllergen.ingredient_id == Ingredient.id)
            .where(ProfileAllergen.profile_id == profile.id)
        )
    ).all()
    return Rules(
        frozenset(i for i, _ in rows),
        frozenset(profile.blocked_tags),
        frozenset(t for _, tags in rows for t in tags),
    )


def status(match: dict, rules: Rules) -> MatchStatus:
    if match["ingredient_id"] in rules.allergen_ids:
        return MatchStatus.ALLERGY
    if match["ingredient"] in GENERIC and rules.allergen_tags & set(match["tags"]):
        return MatchStatus.ALLERGY  # 생선 알레르기면 육수도 확인해야 한다
    if rules.blocked_tags & set(match["tags"]):
        return MatchStatus.AVOID
    return MatchStatus.ALLOWED


def judge(matches: list[dict], rules: Rules) -> Verdict:
    """직원 확인 전 판정. 재료를 하나도 모르면 not_sure"""
    if not (rules.allergen_ids or rules.blocked_tags):
        return Verdict.SAFE
    if not matches:
        return Verdict.NOT_SURE
    hit = {m["likelihood"] for m in matches if status(m, rules) != MatchStatus.ALLOWED}
    if Likelihood.TYPICAL in hit:
        return Verdict.AVOID
    if Likelihood.POSSIBLE in hit:
        return Verdict.NOT_SURE
    return Verdict.SAFE


def flagged(matches: list[dict], rules: Rules) -> list[MenuMatch]:
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
        typical = m["likelihood"] == Likelihood.TYPICAL
        result.append(
            MenuMatch(
                ingredient=m["ingredient"],
                ingredient_en=m["ingredient_en"],
                tag=tag,
                tag_en=TAG_EN.get(tag) if tag else None,
                status=s,
                likelihood=m["likelihood"],
                verdict=Verdict.AVOID if typical else Verdict.NOT_SURE,
            )
        )
    return result
