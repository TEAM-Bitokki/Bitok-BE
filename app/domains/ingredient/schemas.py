from pydantic import BaseModel, Field


class IngredientItem(BaseModel):
    ingredient_id: int = Field(examples=[301])
    name_ko: str = Field(examples=["새우"])
    name_en: str = Field(examples=["Shrimp"])
    matched_alias: str | None = Field(default=None, examples=["새우젓"])


class IngredientSearchResponse(BaseModel):
    items: list[IngredientItem]
