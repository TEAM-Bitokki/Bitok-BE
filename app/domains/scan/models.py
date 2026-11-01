"""스캔 분석 직원 확인"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, BigPK


class Scan(Base):
    """찍은 사진 한 장이 한 행"""

    __tablename__ = "scans"

    id: Mapped[BigPK]

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    # 식당을 고르지 않고 진행하면 NULL
    restaurant_id: Mapped[int | None] = mapped_column(
        ForeignKey("restaurants.id", ondelete="SET NULL"), default=None
    )
    image_url: Mapped[str] = mapped_column(Text)

    # 인식 결과 원문. temp_id raw_name confidence
    raw_ocr: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_scans_user", "user_id", created_at.desc()),)


class Analysis(Base):
    """메뉴 하나의 분석 결과"""

    __tablename__ = "analyses"

    id: Mapped[BigPK]
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"))
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"))
    verdict: Mapped[str] = mapped_column(String(20))
    # [{ingredient, tag, status, likelihood, source}]
    # status = allergy | avoid | allowed
    matches: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")

    # 이 분석에서 실제로 직원에게 물어본 성분 태그
    asked_tags: Mapped[list[str]] = mapped_column(ARRAY(String), server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    scan: Mapped[Scan] = relationship(back_populates="analyses")

    __table_args__ = (
        UniqueConstraint("scan_id", "dish_id", name="uq_scan_dish"),
        CheckConstraint("verdict IN ('avoid','safe','not_sure')", name="ck_verdict"),
    )


class StaffQuestion(Base):
    """직원에게 보여줄 질문"""

    __tablename__ = "staff_questions"

    id: Mapped[BigPK]
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"))
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"))
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    question_ko: Mapped[str] = mapped_column(Text)
    question_en: Mapped[str] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("scan_id", "seq", name="uq_scan_question"),)


class StaffCheck(Base):
    """직원 응답 기록"""

    __tablename__ = "staff_checks"

    id: Mapped[BigPK]
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="CASCADE")
    )
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"))
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE")
    )
    # 탈퇴 시 익명화만
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    answer: Mapped[str] = mapped_column(String(10))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("answer IN ('yes','no','unsure')", name="ck_answer"),
        # 최신순으로 조회
        Index(
            "idx_staff_checks_lookup",
            "restaurant_id",
            "dish_id",
            checked_at.desc(),
        ),
    )
