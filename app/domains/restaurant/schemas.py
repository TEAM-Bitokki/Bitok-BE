from pydantic import BaseModel, Field

from app.common.schemas import DishBrief, Likelihood, MatchStatus, Page, Verdict


class RestaurantBrief(BaseModel):
    """지도 핀과 목록에 쓰는 단위"""

    restaurant_id: int | None = Field(default=None, examples=[7])
    kakao_place_id: str = Field(examples=["26338954"])
    name: str = Field(examples=["맛있는식당"])
    category: str | None = Field(default=None, examples=["한식"])
    address: str | None = Field(default=None, examples=["멋진 주소"])
    lat: float = Field(examples=[37.5633])
    lng: float = Field(examples=[126.9855])
    distance_m: int | None = Field(default=None, examples=[350])
    checked_menu_count: int = Field(default=0, examples=[9])
    is_saved: bool = Field(default=False, examples=[False])


class RestaurantListResponse(BaseModel):
    items: list[RestaurantBrief]


class ResolveRestaurantRequest(BaseModel):
    """nearby search 응답에서 고른 항목. 카카오에서 다시 찾는 데만 쓴다"""

    kakao_place_id: str = Field(min_length=1, max_length=40, examples=["26338954"])
    name: str = Field(min_length=1, max_length=120, examples=["맛있는식당"])
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False, examples=[37.5633])
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False, examples=[126.9855])


class RestaurantDetailResponse(BaseModel):
    restaurant: RestaurantBrief
    checked_menus: list[DishBrief]
    page: Page


class MenuMatch(BaseModel):
    ingredient: str = Field(examples=["새우젓"])
    ingredient_en: str = Field(examples=["Salted Shrimp"])
    tag: str | None = Field(default=None, examples=["crustacean"])
    tag_en: str | None = Field(default=None, examples=["Crustacean Shellfish"])
    status: MatchStatus = Field(examples=["allergy"])
    likelihood: Likelihood = Field(examples=["possible"])
    verdict: Verdict | None = Field(default=None, examples=["avoid"])


class CommonIngredientGroup(BaseModel):
    """Menu Detail의 Common Ingredients 묶음"""

    tag_en: str = Field(examples=["Crustacean Shellfish"])
    ingredients: list[str] = Field(examples=[["Salted shrimp"]])


class PersonalizedMenuItem(DishBrief):
    """For you 목록 한 줄"""

    analysis_id: int = Field(examples=[551])
    verdict: Verdict = Field(examples=["avoid"])


class PersonalizedMenuResponse(BaseModel):
    items: list[PersonalizedMenuItem]


class MenuDetailResponse(BaseModel):
    dish: DishBrief
    verdict: Verdict = Field(examples=["avoid"])
    matches: list[MenuMatch]
    common_ingredients: list[CommonIngredientGroup]
    last_checked_at: str | None = Field(
        default=None,
        examples=["2026-10-28T11:30:00+09:00"],
    )
