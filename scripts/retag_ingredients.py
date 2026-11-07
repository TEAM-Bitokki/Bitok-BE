"""재료 분류를 지금 규칙으로 다시 맞춘다. 규칙을 고친 뒤 배포할 때 돌린다"""

import asyncio

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dictionary import tags_for
from app.common.models import Ingredient
from app.core.db import SessionMaker


async def retag(session: AsyncSession) -> int:
    """바뀐 재료 수를 돌려준다. 커밋은 부르는 쪽이 한다"""
    rows = await session.execute(
        select(Ingredient.id, Ingredient.name_ko, Ingredient.allergen_tags)
    )
    changed = 0
    for i, name, tags in rows.all():
        new = sorted(tags_for(name))
        if new != sorted(tags):
            await session.execute(
                update(Ingredient).where(Ingredient.id == i).values(allergen_tags=new)
            )
            changed += 1
    return changed


async def main() -> None:
    async with SessionMaker() as session:
        changed = await retag(session)
        await session.commit()
        print({"retagged": changed})


if __name__ == "__main__":
    asyncio.run(main())
