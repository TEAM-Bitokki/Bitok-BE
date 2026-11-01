"""익명 기기 ID 기반"""

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, BigPK


class User(Base):
    __tablename__ = "users"

    id: Mapped[BigPK]
    # sha256(uuid v4)
    device_token: Mapped[str] = mapped_column(String(64), unique=True)
    nickname: Mapped[str | None] = mapped_column(String(20), default=None)

    # 지금은 쓰지 않음
    auth_provider: Mapped[str | None] = mapped_column(String(20), default=None)
    provider_uid: Mapped[str | None] = mapped_column(String(128), default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("auth_provider", "provider_uid", name="uq_users_provider"),
    )
