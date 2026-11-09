"""식이 프로필"""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import Ingredient
from app.common.schemas import DietOption, DietType
from app.core.errors import BitokError, ErrorCode
from app.domains.profile.models import DietaryProfile, ProfileAllergen
from app.domains.profile.schemas import AllergenItem, ProfileRequest, ProfileResponse

ANIMAL = {"meat", "fish", "shellfish", "crustacean", "dairy", "egg"}
DIET_BLOCKED = {
    DietType.NONE: set(),
    DietType.VEGAN: ANIMAL | {"honey"},
    DietType.VEGETARIAN: ANIMAL,
    DietType.PESCATARIAN: {"meat"},
}
OPTION_ALLOWS = {DietOption.EATS_EGG: "egg", DietOption.EATS_DAIRY: "dairy"}


def blocked_tags(diet_type: DietType, options: list[DietOption]) -> dict[str, str]:
    """식단에서 막을 분류. 알레르기는 성분 id 로 판정해서 여기 넣지 않는다"""
    tags = DIET_BLOCKED[diet_type] - {OPTION_ALLOWS[o] for o in options}
    return {tag: "diet" for tag in sorted(tags)}


async def get_profile(session: AsyncSession, user_id: int) -> ProfileResponse:
    profile = await session.scalar(
        select(DietaryProfile).where(DietaryProfile.user_id == user_id)
    )
    if profile is None:
        raise BitokError(
            ErrorCode.NOT_FOUND, "식이 프로필이 없습니다.", status_code=404
        )
    rows = await session.execute(
        select(Ingredient.id, Ingredient.name_ko, Ingredient.name_en)
        .join(ProfileAllergen, ProfileAllergen.ingredient_id == Ingredient.id)
        .where(ProfileAllergen.profile_id == profile.id)
        .order_by(ProfileAllergen.id)
    )
    return ProfileResponse(
        allergens=[
            AllergenItem(ingredient_id=i, name_ko=ko, name_en=en) for i, ko, en in rows
        ],
        diet_type=profile.diet_type,
        diet_options=profile.diet_options,
    )


async def put_profile(
    session: AsyncSession, user_id: int, body: ProfileRequest
) -> ProfileResponse:
    """통째로 덮어쓴다"""
    ids = list(dict.fromkeys(body.allergen_ingredient_ids))
    found = set(
        await session.scalars(
            select(Ingredient.id).where(Ingredient.id.in_(ids), Ingredient.searchable)
        )
    )
    unknown = [i for i in ids if i not in found]
    if unknown:
        raise BitokError(
            ErrorCode.VALIDATION_ERROR,
            "고를 수 없는 성분이 있습니다.",
            status_code=400,
            detail={"ingredient_ids": unknown},
        )

    options = body.diet_options if body.diet_type == DietType.VEGETARIAN else []
    values = {
        "diet_type": body.diet_type,
        "diet_options": sorted(set(options)),
        "blocked_tags": blocked_tags(body.diet_type, options),
    }
    profile_id = await session.scalar(
        insert(DietaryProfile)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(
            index_elements=["user_id"], set_={**values, "updated_at": func.now()}
        )
        .returning(DietaryProfile.id)
    )

    await session.execute(
        delete(ProfileAllergen).where(ProfileAllergen.profile_id == profile_id)
    )
    session.add_all(
        ProfileAllergen(profile_id=profile_id, ingredient_id=i) for i in ids
    )
    await session.commit()
    return await get_profile(session, user_id)
