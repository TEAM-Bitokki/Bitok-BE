"""성분, 음식 사전"""

import re
import unicodedata
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, BigPK


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[BigPK]
    name_ko: Mapped[str] = mapped_column(String(80), unique=True)
    name_en: Mapped[str] = mapped_column(String(80))
    # 이 성분이 속한 분류. {'crustacean'}
    # 스무 개 남짓 고정이고 읽기만 하므로 배열로 둔다
    allergen_tags: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    searchable: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    aliases: Mapped[list["IngredientAlias"]] = relationship(
        back_populates="ingredient", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_ingredients_tags", "allergen_tags", postgresql_using="gin"),
    )


class IngredientAlias(Base):
    """새우젓, 건새우, 새우가루 -> 새우

    같은 별칭이 두 성분에 붙으면 판정이 조용히 틀어진다. UNIQUE 로 막는다.
    """

    __tablename__ = "ingredient_aliases"

    id: Mapped[BigPK]
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(80), unique=True)

    ingredient: Mapped[Ingredient] = relationship(back_populates="aliases")


def canonical_name(name: str) -> str:
    """김치 찌개 -> 김치찌개. 시드와 스캔이 같은 규칙을 쓴다"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", name))


_SIZE = r"(대|중|소|특|곱빼기|\d+\s*인분)"
_SIZE_MARK = re.compile(rf"\(\s*{_SIZE}\s*\)|\s{_SIZE}$|\d+\s*인분$")


def menu_name(raw: str) -> str:
    """김치찌개 (대) -> 김치찌개. 띄어 쓰거나 괄호에 든 크기 표시만 뗀다"""
    raw = " ".join(raw.split())
    return _SIZE_MARK.sub("", raw).strip() or raw


def dish_key(raw: str) -> str:
    """메뉴판 이름으로 dishes.canonical_name 을 찾을 때 쓴다"""
    return canonical_name(menu_name(raw))


class Dish(Base):
    """음식 하나하나. 식당별이 아니라 서비스 전체에서 하나"""

    __tablename__ = "dishes"

    id: Mapped[BigPK]
    canonical_name: Mapped[str] = mapped_column(String(120), unique=True)
    name_ko: Mapped[str] = mapped_column(String(120))
    name_en: Mapped[str] = mapped_column(String(120))
    verified: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # 모델에게 재료를 물어본 시각. NULL 이면 아직 안 물어봤다
    described_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    aliases: Mapped[list["DishAlias"]] = relationship(
        back_populates="dish", cascade="all, delete-orphan"
    )
    ingredients: Mapped[list["DishIngredient"]] = relationship(
        back_populates="dish", cascade="all, delete-orphan"
    )


class DishAlias(Base):
    """'김치 찌개' / '김치찌게'(오타). 사용자가 고칠 때마다 쌓인다"""

    __tablename__ = "dish_aliases"

    id: Mapped[BigPK]
    dish_id: Mapped[int] = mapped_column(
        ForeignKey("dishes.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(120), unique=True)

    dish: Mapped[Dish] = relationship(back_populates="aliases")


class DishIngredient(Base):
    """어떤 음식에 어떤 성분이 들어가는지

    줄마다 likelihood, source 가 붙으므로 배열로는 담을 수 없다.
    """

    __tablename__ = "dish_ingredients"

    id: Mapped[BigPK]
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"))
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE")
    )
    # typical 보통 들어감, possible 가게에 따라 들어갈 수 있음
    likelihood: Mapped[str] = mapped_column(String(10))
    # public_data 공공 레시피, AI Hub, llm 런타임 생성, curated 사람이 검수
    source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    dish: Mapped[Dish] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient] = relationship()

    __table_args__ = (
        UniqueConstraint("dish_id", "ingredient_id", name="uq_dish_ingredient"),
        CheckConstraint("likelihood IN ('typical','possible')", name="ck_likelihood"),
        CheckConstraint("source IN ('public_data','llm','curated')", name="ck_source"),
    )
