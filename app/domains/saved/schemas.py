from pydantic import BaseModel, Field

from app.domains.restaurant.schemas import RestaurantBrief


class SaveRestaurantRequest(BaseModel):
    restaurant_id: int = Field(examples=[7])


class SavedRestaurantListResponse(BaseModel):
    items: list[RestaurantBrief]
