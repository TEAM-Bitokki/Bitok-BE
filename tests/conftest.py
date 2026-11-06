"""테스트용 DB. DATABASE_URL 뒤에 `_test`를 붙인 DB"""

import os

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import Base, get_session
from app.main import app
from app.models import Base as _  # noqa: F401  모든 모델을 metadata 에 올린다

def _test_url() -> str:
    """DB 이름에만 `_test` 를 붙인다. CI 는 TEST_DATABASE_URL 로 덮어쓴다

    문자열 뒤에 그냥 붙이면 쿼리 파라미터가 있을 때 이름이 안 바뀌어,
    TRUNCATE 가 개발 DB 를 비우게 된다. 이름이 `_test` 가 아니면 멈춘다.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    url = make_url(explicit or settings.database_url)
    if explicit is None:
        url = url.set(database=f"{url.database}_test")
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(f"테스트 DB 가 아니다: {url.database}")
    return url.render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_url()

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
