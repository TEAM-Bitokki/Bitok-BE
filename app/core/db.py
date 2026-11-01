"""DB 연결과 세션"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import BigInteger, Identity
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, mapped_column

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,  # 끊긴 커넥션을 쓰기 전에 걸러낸다
)

SessionMaker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """모든 모델의 부모. Alembic 이 이 metadata 를 보고 마이그레이션을 만든다."""


# 기본 autoincrement 는 BIGSERIAL 로 나간다. 스키마 규칙에 맞춰 IDENTITY 로 둔다
BigPK = Annotated[
    int, mapped_column(BigInteger, Identity(always=True), primary_key=True)
]


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with SessionMaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
