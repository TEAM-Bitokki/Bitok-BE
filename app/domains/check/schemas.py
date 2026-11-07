from pydantic import BaseModel, Field

from app.common.schemas import DishBrief, Page, Verdict


class SaveRequest(BaseModel):
    scan_id: int = Field(ge=1, le=2**63 - 1, examples=[1021])


class MyCheckItem(DishBrief):
    my_check_id: int = Field(examples=[77])
    analysis_id: int = Field(examples=[551])
    restaurant_id: int | None = Field(default=None, examples=[7])
    restaurant_name: str | None = Field(default=None, examples=["맛있는식당"])
    verdict: Verdict = Field(examples=["safe"])
    saved_at: str = Field(examples=["2026-11-01T11:30:00+09:00"])


class MyCheckListResponse(BaseModel):
    items: list[MyCheckItem]
    page: Page
