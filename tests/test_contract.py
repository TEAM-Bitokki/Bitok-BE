"""프론트 연동을 위한 응답 형식 체크"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

V4 = "550e8400-e29b-41d4-a716-446655440000"
V1 = "2c1b7c0e-b7a4-11ef-9cd2-0242ac120002"
UNREGISTERED = str(uuid.uuid4())  # 다른 테스트가 가입시킨 토큰과 겹치지 않게

pytestmark = (
    pytest.mark.nodb
)  # 테이블을 비우지 않는다. UNREGISTERED 는 가입시키지 않는다

PATH_PARAMS = {
    "{scan_id}": "1",
    "{restaurant_id}": "1",
    "{dish_id}": "1",
    "{my_check_id}": "1",
    "{ingredient_id}": "1",
    "{question_id}": "1",
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def err(response) -> dict:
    """error 봉투 여부"""
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message", "detail"}, body
    return body["error"]


def secured_routes() -> list[tuple[str, str]]:
    """인증이 걸린 엔드포인트"""
    schema = app.openapi()
    out = []
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            if not op.get("security"):
                continue
            url = path
            for k, v in PATH_PARAMS.items():
                url = url.replace(k, v)
            out.append((method.upper(), url))
    assert out, "인증이 걸린 엔드포인트를 찾지 못함."
    return out


def test_every_secured_route_401s_without_token(client):
    """토큰 없이 불렀을때 모두 401인가?"""
    wrong = []
    for method, path in secured_routes():
        r = client.request(method, path, json={})
        if r.status_code != 401 or err(r)["code"] != "UNAUTHORIZED":
            wrong.append(f"{method} {path} -> {r.status_code}")
    assert not wrong, wrong


def test_signup_needs_no_token(client):
    """가입은 헤더 없이 호출되는가?"""
    r = client.post("/api/auth/sign-up", json={"device_token": V4})
    assert r.status_code != 401


@pytest.mark.parametrize("token", [None, "", "abc", "550e8400", V1])
def test_bad_tokens_are_indistinguishable(client, token):
    """토큰 존재 여부를 추측가능한가?"""
    headers = {} if token is None else {"deviceToken": token}
    r = client.get("/api/auth/me", headers=headers)
    registered = client.get("/api/auth/me", headers={"deviceToken": UNREGISTERED})

    assert r.status_code == registered.status_code == 401
    assert err(r) == err(registered)


def test_signup_rejects_non_v4(client):
    r = client.post("/api/auth/sign-up", json={"device_token": V1})
    assert r.status_code == 422
    assert err(r)["code"] == "VALIDATION_ERROR"


def test_validation_error_names_the_field(client):
    r = client.post("/api/auth/sign-up", json={})
    e = err(r)
    assert e["code"] == "VALIDATION_ERROR"
    fields = e["detail"]["fields"]
    assert fields[0]["location"] == "device_token"
    assert fields[0]["where"] == "body"


def test_unknown_path_uses_error_envelope(client):
    """FastAPI 기본이 안 나가는가?"""
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert err(r)["code"] == "NOT_FOUND"


def test_openapi_has_no_fastapi_default_422():
    """HTTPValidationError 가 남는가?"""
    schema = app.openapi()
    assert "HTTPValidationError" not in str(schema)
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            r422 = op.get("responses", {}).get("422")
            if r422:
                ref = r422["content"]["application/json"]["schema"]["$ref"]
                assert ref.endswith("/ErrorResponse"), f"{method} {path}"
