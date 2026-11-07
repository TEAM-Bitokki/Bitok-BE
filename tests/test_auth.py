"""가입·조회·탈퇴"""

import pytest
from sqlalchemy import select

from app.core.errors import BitokError
from app.core.security import hash_token
from app.domains.auth import service
from app.domains.auth.models import User
from app.models import Analysis, Dish, MyCheck, Restaurant, Scan

V4 = "550e8400-e29b-41d4-a716-446655440000"
H = {"deviceToken": V4}


def test_sign_up_creates_account(client):
    r = client.post("/api/auth/sign-up", json={"device_token": V4, "nickname": "Sarah"})
    assert r.status_code == 200
    body = r.json()
    assert body["nickname"] == "Sarah"
    assert body["has_profile"] is False
    assert body["allergen_count"] == 0


def test_sign_up_trims_nickname(client):
    r = client.post(
        "/api/auth/sign-up", json={"device_token": V4, "nickname": "  Sarah  "}
    )
    assert r.status_code == 200
    assert r.json()["nickname"] == "Sarah"


def test_sign_up_is_idempotent(client):
    """앱이 재시도해도 계정이 둘로 늘지 않아야 한다"""
    first = client.post("/api/auth/sign-up", json={"device_token": V4}).json()
    again = client.post(
        "/api/auth/sign-up", json={"device_token": V4, "nickname": "다른이름"}
    ).json()
    assert first["user_id"] == again["user_id"]


def test_sign_up_accepts_token_without_hyphens(client):
    """표기가 달라도 같은 계정이어야 한다"""
    a = client.post("/api/auth/sign-up", json={"device_token": V4}).json()
    b = client.post(
        "/api/auth/sign-up", json={"device_token": V4.replace("-", "")}
    ).json()
    assert a["user_id"] == b["user_id"]


@pytest.mark.asyncio
async def test_token_is_stored_hashed(client, session):
    """원문이 DB 에 남으면 안 된다"""
    client.post("/api/auth/sign-up", json={"device_token": V4})
    stored = await session.scalar(select(User.device_token))
    assert stored != V4
    assert stored == hash_token(V4)


@pytest.mark.asyncio
async def test_sign_up_survives_concurrent_insert(client, session, monkeypatch):
    """같은 토큰이 동시에 가입해도 500 이 아니라 기존 계정을 돌려준다"""
    existing = User(device_token=hash_token(V4))
    session.add(existing)
    await session.commit()

    real = service.find_user_id
    calls = {"n": 0}

    async def first_lookup_misses(*args):
        """조회를 지난 직후 다른 요청이 먼저 넣은 상황"""
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(*args)

    monkeypatch.setattr(service, "find_user_id", first_lookup_misses)

    r = client.post("/api/auth/sign-up", json={"device_token": V4})
    assert r.status_code == 200
    assert r.json()["user_id"] == existing.id


@pytest.mark.asyncio
async def test_get_me_401s_when_account_is_gone(session):
    """인증을 지난 뒤 다른 요청이 탈퇴시킨 경우"""
    with pytest.raises(BitokError) as caught:
        await service.get_me(session, 999)
    assert caught.value.status_code == 401


def test_me_requires_registered_token(client):
    assert client.get("/api/auth/me", headers=H).status_code == 401
    client.post("/api/auth/sign-up", json={"device_token": V4})
    assert client.get("/api/auth/me", headers=H).status_code == 200


def test_update_nickname(client):
    client.post(
        "/api/auth/sign-up", json={"device_token": V4, "nickname": "Sarah"}
    )

    r = client.patch(
        "/api/users/me", headers=H, json={"nickname": "현영"}
    )

    assert r.status_code == 200
    assert r.json()["nickname"] == "현영"
    assert client.get("/api/auth/me", headers=H).json()["nickname"] == "현영"


def test_update_nickname_requires_registered_token(client):
    r = client.patch(
        "/api/users/me", headers=H, json={"nickname": "현영"}
    )
    assert r.status_code == 401


def test_update_nickname_rejects_more_than_20_characters(client):
    client.post("/api/auth/sign-up", json={"device_token": V4})

    r = client.patch(
        "/api/users/me", headers=H, json={"nickname": "가" * 21}
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_update_nickname_trims_whitespace(client):
    client.post("/api/auth/sign-up", json={"device_token": V4})

    r = client.patch(
        "/api/users/me", headers=H, json={"nickname": "  현영  "}
    )

    assert r.status_code == 200
    assert r.json()["nickname"] == "현영"


@pytest.mark.parametrize("nickname", ["", " ", "\t\n"])
def test_update_nickname_rejects_blank_value(client, nickname):
    client.post("/api/auth/sign-up", json={"device_token": V4})

    r = client.patch(
        "/api/users/me", headers=H, json={"nickname": nickname}
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_update_nickname_commits_after_response_is_built(
    session, monkeypatch
):
    user = User(device_token=hash_token(V4), nickname="기존닉네임")
    session.add(user)
    await session.commit()

    async def fail_to_build_response(*args):
        raise RuntimeError("응답 생성 실패")

    monkeypatch.setattr(service, "get_me", fail_to_build_response)

    with pytest.raises(RuntimeError):
        await service.update_nickname(session, user.id, "새닉네임")

    await session.rollback()
    await session.refresh(user)
    assert user.nickname == "기존닉네임"


def test_delete_needs_agreement(client):
    client.post("/api/auth/sign-up", json={"device_token": V4})
    r = client.request("DELETE", "/api/users/me", headers=H, json={"agree": False})
    assert r.status_code == 400
    assert client.get("/api/auth/me", headers=H).status_code == 200


def test_delete_removes_account(client):
    client.post("/api/auth/sign-up", json={"device_token": V4})
    r = client.request("DELETE", "/api/users/me", headers=H, json={"agree": True})
    assert r.status_code == 204
    assert client.get("/api/auth/me", headers=H).status_code == 401


@pytest.mark.asyncio
async def test_delete_keeps_public_records(client, session):
    """delete_saved_menus=false 면 기록이 익명화되어 남는다"""
    user_id = client.post("/api/auth/sign-up", json={"device_token": V4}).json()["user_id"]
    await _seed_records(session, user_id)

    client.request(
        "DELETE", "/api/users/me", headers=H,
        json={"agree": True, "delete_saved_menus": False},
    )

    scan = await session.scalar(select(Scan))
    check = await session.scalar(select(MyCheck))
    assert scan is not None and scan.user_id is None
    assert check is not None and check.user_id is None


@pytest.mark.asyncio
async def test_delete_can_remove_records(client, session):
    """delete_saved_menus=true 면 분석 기록까지 사라진다"""
    user_id = client.post("/api/auth/sign-up", json={"device_token": V4}).json()["user_id"]
    await _seed_records(session, user_id)

    client.request(
        "DELETE", "/api/users/me", headers=H,
        json={"agree": True, "delete_saved_menus": True},
    )

    assert await session.scalar(select(Scan)) is None
    assert await session.scalar(select(Analysis)) is None
    assert await session.scalar(select(MyCheck)) is None


@pytest.mark.asyncio
async def test_delete_removes_checks_on_others_analysis(client, session):
    """남의 분석을 저장한 행도 지워야 한다. scans 만 지우면 남는다"""
    user_id = client.post("/api/auth/sign-up", json={"device_token": V4}).json()["user_id"]
    other = User(device_token=hash_token("other"))
    session.add(other)
    await session.flush()
    other_id = other.id
    analysis_id, rest_id = await _seed_records(session, other_id)
    session.add(MyCheck(user_id=user_id, analysis_id=analysis_id, restaurant_id=rest_id))
    await session.commit()

    client.request(
        "DELETE", "/api/users/me", headers=H,
        json={"agree": True, "delete_saved_menus": True},
    )

    # 탈퇴한 행은 user_id 가 NULL 로 바뀌므로 user_id 로 찾으면 안 된다
    owners = (await session.scalars(select(MyCheck.user_id))).all()
    assert owners == [other_id]
    assert await session.scalar(select(Scan)) is not None   # 남의 기록은 그대로


async def _seed_records(session, user_id: int) -> tuple[int, int]:
    dish = Dish(canonical_name="kimchijjigae", name_ko="김치찌개", name_en="Kimchi Jjigae")
    rest = Restaurant(kakao_place_id="1", name="명동교자", lat=37.5, lng=127.0)
    session.add_all([dish, rest])
    await session.flush()
    scan = Scan(user_id=user_id, restaurant_id=rest.id, image_url="x")
    session.add(scan)
    await session.flush()
    analysis = Analysis(scan_id=scan.id, dish_id=dish.id, verdict="safe")
    session.add(analysis)
    await session.flush()
    session.add(MyCheck(user_id=user_id, analysis_id=analysis.id, restaurant_id=rest.id))
    await session.commit()
    return analysis.id, rest.id
