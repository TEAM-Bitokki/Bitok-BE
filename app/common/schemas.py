"""여러 도메인에서 함께 쓰는 타입들"""

from enum import StrEnum

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    """메뉴 판정 상태"""

    AVOID = "avoid"  # 들어가요
    SAFE = "safe"  # 안 들어가요
    NOT_SURE = "not_sure"  # 모르겠어요


class MatchStatus(StrEnum):
    """성분 체크 사유"""

    ALLERGY = "allergy"  # 알레르기
    AVOID = "avoid"  # 식이조건
    ALLOWED = "allowed"  # 걸리지 않음


class Likelihood(StrEnum):
    TYPICAL = "typical"  # 보통 들어감
    POSSIBLE = "possible"  # 가게에 따라 달라짐


class IngredientSource(StrEnum):
    PUBLIC_DATA = "public_data"  # 공공 레시피 데이터
    LLM = "llm"  # 모델이 생성
    CURATED = "curated"  # 사람이 검수


class DietType(StrEnum):
    NONE = "none"
    VEGAN = "vegan"
    VEGETARIAN = "vegetarian"
    PESCATARIAN = "pescatarian"


class StaffAnswer(StrEnum):
    YES = "yes"  # 들어가요
    NO = "no"  # 안 들어가요
    UNSURE = "unsure"  # 모르겠어요


class DishBrief(BaseModel):
    """목록에 반복해서 나오는 음식 표시 단위"""

    dish_id: int = Field(examples=[12])
    name_ko: str = Field(examples=["김치찌개"])
    name_en: str = Field(examples=["Kimchi Jjigae"])


class Page(BaseModel):
    """무한 스크롤용 커서 페이지네이션"""

    next_cursor: str | None = Field(default=None, examples=["eyJpZCI6MTIzfQ=="])
    has_more: bool = Field(examples=[True])
