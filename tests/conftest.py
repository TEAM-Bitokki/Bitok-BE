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
from app.domains.scan.models import Scan
from app.main import app
from app.models import Base as _  # noqa: F401  모든 모델을 metadata 에 올린다
from scripts.seed_dictionary import build, seed


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

# 테스트 전체가 이벤트 루프 하나를 쓰고, 연결도 그 안에서 재사용한다.
# 윈도우 Postgres 는 연결 하나에 0.1초 걸려서 테스트마다 새로 열면 느리다
test_engine = create_async_engine(TEST_DATABASE_URL, pool_size=5, connect_args=FAST)
TestEngineSession = async_sessionmaker(test_engine, expire_on_commit=False)

# 앱은 TestClient 의 이벤트 루프에서 돈다. 그 루프 전용
app_engine = create_async_engine(TEST_DATABASE_URL, pool_size=5, connect_args=FAST)
AppSession = async_sessionmaker(app_engine, expire_on_commit=False)

# 어느 이벤트 루프에서든 쓸 수 있는 연결. 대신 매번 새로 연다
engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, connect_args=FAST)
TestSessionMaker = async_sessionmaker(engine, expire_on_commit=False)

_created = False
_WIPE = "; ".join(
    f'DELETE FROM "{t.name}"' for t in reversed(Base.metadata.sorted_tables)
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _close_test_engine():
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(request):
    """테스트마다 빈 테이블로 시작. DB 를 안 쓰면 건너뛰고, nodb 면 비우지만 않는다"""
    global _created
    if not {"client", "session"} & set(request.fixturenames):
        yield
        return
    nodb = request.node.get_closest_marker("nodb")
    if not (_created and nodb):
        async with test_engine.begin() as conn:
            if not _created:  # 모델에서 빠진 테이블까지 지운다
                rows = await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
                for (table,) in rows:
                    await conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
                await conn.run_sync(Base.metadata.create_all)
                _created = True
            if not nodb:
                # 몇 줄 안 되는 테이블은 TRUNCATE 보다 DELETE 가 빠르다. 한 번에 보낸다
                raw = await conn.get_raw_connection()
                await raw.driver_connection.execute(_WIPE)
    yield


async def _override_session():
    async with AppSession() as session:
        yield session


app.dependency_overrides[get_session] = _override_session


@pytest.fixture(scope="session")
def _app_client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
        c.portal.call(app_engine.dispose)


@pytest.fixture
def client(_app_client):
    return _app_client


@pytest_asyncio.fixture
async def session():
    async with TestEngineSession() as s:
        yield s


@pytest.fixture
def other_session():
    """다른 이벤트 루프(앱 안)에서 따로 여는 세션"""
    return TestSessionMaker


@pytest_asyncio.fixture
async def scan(client, session, monkeypatch):
    """가입한 사용자의 빈 스캔. 김치찌개가 시드에 있다 (돼지고기, 두부가 보통 들어감)"""
    monkeypatch.setattr(settings, "mock_llm", False)
    kimchi = {
        "food_type": "한식",
        "ko": "김치찌개",
        "en.ROMAN": "kimchijjigae",
        "ingredients.ko": ["돼지고기", "두부"],
        "ingredients.en": ["Pork", "Tofu"],
        "allergy": [],
    }
    await seed(session, build([kimchi] * 2))
    r = client.post(
        "/api/auth/sign-up",
        json={"device_token": "550e8400-e29b-41d4-a716-446655440000"},
    )
    s = Scan(user_id=r.json()["user_id"], image_url="x.jpg")
    session.add(s)
    await session.commit()
    return s.id
