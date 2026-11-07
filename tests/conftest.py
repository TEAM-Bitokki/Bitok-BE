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

# 커밋마다 디스크 기록을 기다리지 않는다. 테스트 DB 에만
FAST = {"server_settings": {"synchronous_commit": "off"}}

# 어느 이벤트 루프에서든 쓸 수 있는 연결. 대신 매번 새로 연다
engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, connect_args=FAST)
TestSessionMaker = async_sessionmaker(engine, expire_on_commit=False)


def _pooled() -> tuple:
    """테스트 하나 동안 연결을 재사용한다. 윈도우 Postgres 는 연결 하나에 0.1초 걸린다

    연결은 만든 이벤트 루프에서만 쓸 수 있어서 테스트마다 새로 만들고 끝나면 닫는다.
    """
    e = create_async_engine(TEST_DATABASE_URL, pool_size=5, connect_args=FAST)
    return e, async_sessionmaker(e, expire_on_commit=False)


_current = {"test": TestSessionMaker, "app": TestSessionMaker}

_created = False


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(request):
    """테스트마다 빈 테이블로 시작. DB 를 안 쓰면 건너뛰고, nodb 면 비우지만 않는다"""
    global _created
    if not {"client", "session"} & set(request.fixturenames):
        yield
        return
    nodb = request.node.get_closest_marker("nodb")
    pooled, _current["test"] = _pooled()
    if _created and nodb:
        yield
        await pooled.dispose()
        return
    async with pooled.begin() as conn:
        if not _created:  # 모델에서 빠진 테이블까지 지운다
            rows = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            for (table,) in rows:
                await conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
            await conn.run_sync(Base.metadata.create_all)
            _created = True
        if not nodb:
            # 몇 줄 안 되는 테이블은 TRUNCATE 보다 DELETE 가 훨씬 빠르다
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())
    yield
    await pooled.dispose()
    _current["test"] = TestSessionMaker


async def _override_session():
    async with _current["app"]() as session:
        yield session


app.dependency_overrides[get_session] = _override_session


@pytest.fixture
def client():
    """앱도 테스트 하나 동안 같은 이벤트 루프와 연결을 쓴다"""
    pooled, _current["app"] = _pooled()
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
            c.portal.call(pooled.dispose)
    finally:
        _current["app"] = TestSessionMaker


@pytest_asyncio.fixture
async def session():
    async with _current["test"]() as s:
        yield s


@pytest.fixture
def other_session():
    """다른 이벤트 루프(앱 안)에서 따로 여는 세션"""
    return TestSessionMaker
