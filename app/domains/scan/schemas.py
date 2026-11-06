from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.common.schemas import DishBrief, StaffAnswer, Verdict
from app.domains.restaurant.schemas import MenuMatch


class RecognizedItem(BaseModel):
    """OCR 이 읽은 메뉴 한 줄"""

    temp_id: str = Field(examples=["i1"])
    raw_name: str = Field(examples=["김치찌개"])
    name_en: str | None = Field(default=None, examples=["Kimchi Jjigae"])
    confidence: float = Field(ge=0, le=1, examples=[0.94])


class ScanResponse(BaseModel):
    scan_id: int = Field(examples=[1021])
    restaurant_id: int | None = Field(default=None, examples=[7])
    items: list[RecognizedItem]
    image_quality: str = Field(examples=["ok"])


TempId = Annotated[str, StringConstraints(min_length=1, max_length=20)]


class AnalyzeItem(BaseModel):
    temp_id: TempId = Field(examples=["i1"])
    raw_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] = Field(examples=["김치찌개"])


class AnalyzeRequest(BaseModel):
    """사용자가 수정, 추가, 삭제를 마친 최종 목록과, 분석할 항목"""

    items: list[AnalyzeItem] = Field(max_length=100)
    selected_temp_ids: list[TempId] = Field(
        min_length=1, max_length=100, examples=[["i1", "i2", "i3"]]
    )


class AnalyzedMenu(DishBrief):
    analysis_id: int = Field(examples=[551])
    verdict: Verdict = Field(examples=["not_sure"])
    matches: list[MenuMatch]


class StaffQuestion(BaseModel):
    question_id: int = Field(examples=[880])
    seq: int = Field(examples=[1])
    total: int = Field(examples=[3])
    dish_id: int = Field(examples=[12])
    ingredient_id: int = Field(examples=[301])
    question_ko: str = Field(
        examples=["이 김치찌개에 새우가 들어가나요?"],
    )
    question_en: str = Field(examples=["Does this kimchi jjigae contain shrimp?"])


class AnalyzeResponse(BaseModel):
    menus: list[AnalyzedMenu]
    questions: list[StaffQuestion]


class AnswerRequest(BaseModel):
    question_id: int = Field(examples=[880])
    answer: StaffAnswer = Field(examples=["yes"])


class ResultResponse(BaseModel):
    """Staff Check Results 화면"""

    scan_id: int = Field(examples=[1021])
    restaurant_id: int | None = Field(default=None, examples=[7])
    restaurant_name: str | None = Field(default=None, examples=["맛있는식당"])
    menus: list[AnalyzedMenu]
    checked_at: str = Field(examples=["2026-11-01T11:30:00+09:00"])
