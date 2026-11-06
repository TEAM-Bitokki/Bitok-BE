"""성분 검색"""

import unicodedata

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import Ingredient, IngredientAlias
from app.domains.ingredient.schemas import IngredientItem


def _rank(text: str | None, q: str) -> int:
    """같으면 0, 앞이 같으면 1, 포함이면 2, 아니면 3"""
    text = (text or "").lower()
    if text == q:
        return 0
    if text.startswith(q):
        return 1
    return 2 if q in text else 3


async def search(session: AsyncSession, q: str, limit: int) -> list[IngredientItem]:
    q = unicodedata.normalize("NFC", q).strip().lower()  # DB 는 NFC
    if not q:
        return []
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    rows = await session.execute(
        select(
            Ingredient.id, Ingredient.name_ko, Ingredient.name_en, IngredientAlias.alias
        )
        .outerjoin(IngredientAlias, IngredientAlias.ingredient_id == Ingredient.id)
        .where(
            Ingredient.searchable,
            or_(
                Ingredient.name_ko.ilike(pattern, escape="\\"),
                Ingredient.name_en.ilike(pattern, escape="\\"),
                IngredientAlias.alias.ilike(pattern, escape="\\"),
            ),
        )
    )

    best: dict[int, tuple[int, IngredientItem]] = {}
    for id_, ko, en, alias in rows:
        by_name = min(_rank(ko, q), _rank(en, q))
        by_alias = _rank(alias, q)
        rank, via = (by_name, None) if by_name <= by_alias else (by_alias, alias)
        if id_ not in best or rank < best[id_][0]:
            item = IngredientItem(
                ingredient_id=id_, name_ko=ko, name_en=en, matched_alias=via
            )
            best[id_] = (rank, item)

    ordered = sorted(
        best.values(), key=lambda r: (r[0], len(r[1].name_ko), r[1].name_ko)
    )
    return [item for _, item in ordered[:limit]]
