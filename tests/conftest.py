"""테스트용 DB. DATABASE_URL 뒤에 `_test`를 붙인 DB"""

import os

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import Base, get_session
from app.main import app
from app.models import Base as _

TEST_DATABASE_URL = (
    os.environ.get("TEST_DATABASE_URL") or f"{settings.database_url}_test"
)

engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
TestSessionMaker = async_sessionmaker(engine, expire_on_commit=False)

_TABLES = ", ".join(Base.metadata.tables)
_created = False


@pytest_asyncio.fixture(autouse=True)
async def _clean_db():
    """테스트마다 빈 테이블로 시작"""
    global _created
    async with engine.begin() as conn:
        if not _created:
            await conn.run_sync(Base.metadata.create_all)
            _created = True
        await conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    yield


async def _override_session():
    async with TestSessionMaker() as session:
        yield session


app.dependency_overrides[get_session] = _override_session


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest_asyncio.fixture
async def session():
    async with TestSessionMaker() as s:
        yield s
