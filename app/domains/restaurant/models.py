"""식당"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, BigPK


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[BigPK]
    kakao_place_id: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(60), default=None)
    address: Mapped[str | None] = mapped_column(String(200), default=None)
    lat: Mapped[Decimal] = mapped_column(Numeric(10, 7))
    lng: Mapped[Decimal] = mapped_column(Numeric(10, 7))
    # 지도 핀에 뜨는 숫자
    checked_menu_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # PostGIS ㄴㄴ
    __table_args__ = (Index("idx_restaurants_geo", "lat", "lng"),)


class RestaurantMenuItem(Base):
    """식당 메뉴"""

    __tablename__ = "restaurant_menu_items"

    id: Mapped[BigPK]
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE")
    )
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"))
    # 표기 통일이 틀렸을 때
    raw_ocr_name: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("restaurant_id", "dish_id", name="uq_restaurant_dish"),
    )
