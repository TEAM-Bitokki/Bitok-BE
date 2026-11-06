from pydantic import BaseModel, Field


class IngredientItem(BaseModel):
    ingredient_id: int = Field(examples=[7])
    name_ko: str = Field(examples=["계란"])
    name_en: str = Field(examples=["Egg"])
    matched_alias: str | None = Field(default=None, examples=["달걀"])


class IngredientSearchResponse(BaseModel):
    items: list[IngredientItem]
