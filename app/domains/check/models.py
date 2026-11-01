"""저장한 메뉴"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, BigPK


class MyCheck(Base):
    __tablename__ = "my_checks"

    id: Mapped[BigPK]
    # 탈퇴 시 '저장 메뉴도 함께 삭제'가 선택 항목
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE")
    )
    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="SET NULL"), default=None
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "analysis_id", name="uq_my_check"),
        Index("idx_my_checks_user", "user_id", saved_at.desc()),
        Index("idx_my_checks_restaurant", "restaurant_id", saved_at.desc()),
    )
