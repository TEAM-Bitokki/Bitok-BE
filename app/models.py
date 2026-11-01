"""모든 모델을 한 번에 import 한다.

Alembic 이 Base.metadata 로 마이그레이션을 만들기 위해 모델을 추가하면 여기에도 넣는다."""

from app.common.models import (
    Dish,
    DishAlias,
    DishIngredient,
    Ingredient,
    IngredientAlias,
)
from app.core.db import Base
from app.domains.auth.models import User
from app.domains.check.models import MyCheck
from app.domains.profile.models import DietaryProfile, ProfileAllergen
from app.domains.restaurant.models import Restaurant, RestaurantMenuItem
from app.domains.saved.models import SavedRestaurant
from app.domains.scan.models import Analysis, Scan, StaffCheck, StaffQuestion

__all__ = [
    "Analysis",
    "Base",
    "DietaryProfile",
    "Dish",
    "DishAlias",
    "DishIngredient",
    "Ingredient",
    "IngredientAlias",
    "MyCheck",
    "ProfileAllergen",
    "Restaurant",
    "RestaurantMenuItem",
    "SavedRestaurant",
    "Scan",
    "StaffCheck",
    "StaffQuestion",
    "User",
]
