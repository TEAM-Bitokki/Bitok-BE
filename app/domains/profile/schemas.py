from typing import Annotated

from pydantic import BaseModel, Field

from app.common.schemas import DietOption, DietType, Severity


IngredientId = Annotated[int, Field(ge=1, le=2**63 - 1)]  # BIGINT


class AllergenItem(BaseModel):
    ingredient_id: int = Field(examples=[301])
    name_ko: str = Field(examples=["새우"])
    name_en: str = Field(examples=["Shrimp"])


class ProfileResponse(BaseModel):
    allergens: list[AllergenItem]
    diet_type: DietType = Field(examples=["vegetarian"])
    diet_options: list[DietOption] = Field(
        default_factory=list,
        examples=[["eats_egg", "eats_dairy"]],
    )
    severity: Severity = Field(default=Severity.NORMAL, examples=["normal"])
    cross_contamination: bool = Field(default=False, examples=[False])


class ProfileRequest(BaseModel):
    """온보딩과 수정 양쪽에서 쓴다. 통째로 덮어쓴다."""

    allergen_ingredient_ids: list[IngredientId] = Field(
        max_length=100, examples=[[301, 305, 412]]
    )
    diet_type: DietType = Field(examples=["vegetarian"])
    diet_options: list[DietOption] = Field(
        default_factory=list, examples=[["eats_egg"]]
    )
    severity: Severity = Field(default=Severity.NORMAL, examples=["normal"])
    cross_contamination: bool = Field(default=False, examples=[False])
