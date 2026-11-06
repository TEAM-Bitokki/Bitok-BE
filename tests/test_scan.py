"""메뉴판 촬영 + 인식"""

import asyncio
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from google.genai import errors
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import Dish
from app.core.config import settings
from app.domains.scan import ocr, service
from app.domains.scan.models import Scan
from app.domains.scan.ocr import MenuExtraction, MenuItem

V4 = "550e8400-e29b-41d4-a716-446655440000"
H = {"deviceToken": V4}


def jpeg(size=64) -> bytes:
    buf = BytesIO()
    Image.effect_noise((size, size), 64).convert("RGB").save(buf, "JPEG")
    return buf.getvalue()


JPEG = jpeg()
BROKEN = jpeg(400)[:20_000]  # 헤더는 멀쩡하고 뒤가 잘림


def huge_png() -> bytes:
    """파일은 작은데 풀면 2500만 픽셀"""
    buf = BytesIO()
    Image.new("1", (5000, 5000)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def user(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "mock_llm", False)
    client.post("/api/auth/sign-up", json={"device_token": V4})
    client.put(
        "/api/profiles/me",
        headers=H,
        json={"allergen_ingredient_ids": [], "diet_type": "none"},
    )
    return tmp_path


def ocr_returns(monkeypatch, *items, quality="ok"):
    async def fake(image, mime):
        return MenuExtraction(items=list(items), image_quality=quality)

    monkeypatch.setattr(service, "extract_menu", fake)


def upload(client, data=JPEG, **form):
    return client.post(
        "/api/scans", headers=H, files={"image": ("menu.jpg", data)}, data=form
    )


@pytest.mark.asyncio
async def test_scan_reads_menu_and_saves(client, session, user, monkeypatch):
    session.add(
        Dish(canonical_name="김치찌개", name_ko="김치찌개", name_en="Kimchijjigae")
    )
    await session.commit()
    ocr_returns(
        monkeypatch,
        MenuItem(raw_name="김치 찌개", name_en="Kimchi Stew", confidence=0.9),
        MenuItem(raw_name="사장님특제", name_en="Sajangnim Teukje", confidence=0.6),
        MenuItem(raw_name="공기밥", name_en="  ", confidence=0.9),
        MenuItem(raw_name="에그햄 샌드위치\n+샐러드", name_en="A\nB", confidence=0.9),
    )

    r = upload(client)

    assert r.status_code == 201
    items = r.json()["items"]
    assert [i["temp_id"] for i in items] == ["i1", "i2", "i3", "i4"]
    assert items[0]["name_en"] == "Kimchijjigae"  # 사전 표기가 이긴다
    assert items[1]["name_en"] == "Sajangnim Teukje"
    assert items[2]["name_en"] is None  # 빈 로마자는 비워서 보낸다
    assert items[3]["raw_name"] == "에그햄 샌드위치 +샐러드"
    assert items[3]["name_en"] == "A B"

    scan = await session.scalar(select(Scan))
    assert scan.raw_ocr["items"][0]["raw_name"] == "김치 찌개"
    assert (Path(user) / scan.image_url).read_bytes() == JPEG


@pytest.mark.parametrize(
    "data",
    [b"not an image", BROKEN, JPEG + b"0" * 1024 * 1024, huge_png()],
    ids=["텍스트", "깨진 JPEG", "1MB 초과", "픽셀 과다"],
)
def test_scan_rejects_bad_image(client, user, monkeypatch, data):
    monkeypatch.setattr(settings, "max_image_mb", 1)
    r = upload(client, data)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_IMAGE"


def test_scan_requires_profile(client, user):
    token = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"
    client.post("/api/auth/sign-up", json={"device_token": token})
    r = client.post(
        "/api/scans", headers={"deviceToken": token}, files={"image": ("m.jpg", JPEG)}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "PROFILE_REQUIRED"


@pytest.mark.parametrize(
    "items,code",
    [
        ([], "NO_MENU_DETECTED"),
        ([MenuItem(raw_name="  ", name_en="", confidence=0.9)], "NO_MENU_DETECTED"),
        (
            [MenuItem(raw_name="국밥", name_en="Gukbap", confidence=0.3)],
            "OCR_LOW_CONFIDENCE",
        ),
    ],
)
@pytest.mark.asyncio
async def test_scan_fails_without_readable_menu(
    client, session, user, monkeypatch, items, code
):
    ocr_returns(monkeypatch, *items)
    r = upload(client)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == code
    assert await session.scalar(select(Scan)) is None


def test_unknown_restaurant_404(client, user, monkeypatch):
    ocr_returns(
        monkeypatch, MenuItem(raw_name="국밥", name_en="Gukbap", confidence=0.9)
    )
    assert upload(client, restaurant_id="999").status_code == 404


def test_llm_timeout_falls_back(client, user, monkeypatch):
    calls = []

    async def slow(image, mime, model):
        calls.append(model)
        await asyncio.sleep(10)

    monkeypatch.setattr(settings, "llm_timeout_sec", 0.05)
    monkeypatch.setattr(ocr, "_call", slow)
    r = upload(client)
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "LLM_TIMEOUT"
    assert calls == [settings.gemini_model, settings.gemini_fallback_model]


def test_fallback_model_answers(client, user, monkeypatch):
    """기본 모델이 503 이어도 예비 모델이 읽으면 성공이다"""

    async def busy(image, mime, model):
        if model == settings.gemini_model:
            raise errors.ServerError(503, {"error": {"message": "high demand"}})
        return MenuExtraction(
            items=[MenuItem(raw_name="국밥", name_en="Gukbap", confidence=0.9)],
            image_quality="ok",
        )

    monkeypatch.setattr(ocr, "_call", busy)
    r = upload(client)
    assert r.status_code == 201
    assert r.json()["items"][0]["raw_name"] == "국밥"


def test_mock_llm_needs_no_key(client, user, monkeypatch):
    """프론트 연동용. 키 없이도 고정 응답이 나온다"""
    monkeypatch.setattr(settings, "mock_llm", True)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    r = upload(client)
    assert r.status_code == 201
    assert [i["raw_name"] for i in r.json()["items"]] == [
        "김치찌개",
        "된장찌개",
        "제육볶음",
    ]


@pytest.mark.parametrize(
    "error,tries",
    [
        (errors.ClientError(400, {"error": {"message": "key"}}), 1),
        (errors.ClientError(429, {"error": {"message": "rate"}}), 2),
        (httpx.ConnectError("down"), 2),
    ],
    ids=["400", "429", "연결 끊김"],
)
def test_llm_errors_retry_only_transient(client, user, monkeypatch, error, tries):
    """키가 틀린 건 다시 불러도 같고, 요청 제한·연결 끊김은 풀릴 수 있다"""
    calls = []

    async def fail(image, mime, model):
        calls.append(1)
        raise error

    monkeypatch.setattr(ocr, "_call", fail)
    r = upload(client)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "LLM_UNAVAILABLE"
    assert len(calls) == tries


def test_commit_failure_removes_image(client, user, monkeypatch):
    ocr_returns(
        monkeypatch, MenuItem(raw_name="국밥", name_en="Gukbap", confidence=0.9)
    )

    async def broken(self):
        raise RuntimeError("db down")

    monkeypatch.setattr(AsyncSession, "commit", broken)
    assert upload(client).status_code == 500
    assert list(Path(user).rglob("*.jpg")) == []


def test_delete_account_removes_images(client, user, monkeypatch):
    """분석 기록까지 지우는 탈퇴면 사진도 지운다"""
    ocr_returns(
        monkeypatch, MenuItem(raw_name="국밥", name_en="Gukbap", confidence=0.9)
    )
    upload(client)
    assert list(Path(user).rglob("*.jpg"))

    client.request(
        "DELETE",
        "/api/users/me",
        headers=H,
        json={"agree": True, "delete_saved_menus": True},
    )
    assert list(Path(user).rglob("*.jpg")) == []
