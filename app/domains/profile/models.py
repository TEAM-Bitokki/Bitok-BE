"""식이 프로필"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, BigPK


class DietaryProfile(Base):
    __tablename__ = "dietary_profiles"

    id: Mapped[BigPK]
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    diet_type: Mapped[str] = mapped_column(String(20), server_default="none")
    # Vegetarian 하위 질문 결과 {'eats_egg','eats_dairy'}
    diet_options: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    severity: Mapped[str] = mapped_column(String(20), server_default="normal")
    cross_contamination: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # 최종적으로 피해야 할 분류
    blocked_tags: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    allergens: Mapped[list["ProfileAllergen"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "diet_type IN ('none','vegan','vegetarian','pescatarian')",
            name="ck_diet_type",
        ),
        CheckConstraint("severity IN ('normal','severe')", name="ck_severity"),
    )


class ProfileAllergen(Base):
    """사용자가 검색해서 직접 고른 알레르기 성분"""

    __tablename__ = "profile_allergens"

    id: Mapped[BigPK]
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("dietary_profiles.id", ondelete="CASCADE")
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE")
    )

    profile: Mapped[DietaryProfile] = relationship(back_populates="allergens")

    __table_args__ = (
        UniqueConstraint("profile_id", "ingredient_id", name="uq_profile_allergen"),
    )
