"""메뉴판 촬영 + 인식"""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.common.models import Dish
from app.core.config import settings
from app.domains.scan import ocr, service
from app.domains.scan.models import Scan
from app.domains.scan.ocr import MenuExtraction, MenuItem

V4 = "550e8400-e29b-41d4-a716-446655440000"
H = {"deviceToken": V4}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


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
    )

    r = upload(client)

    assert r.status_code == 201
    items = r.json()["items"]
    assert [i["temp_id"] for i in items] == ["i1", "i2"]
    assert items[0]["name_en"] == "Kimchijjigae"  # 사전 표기가 이긴다
    assert items[1]["name_en"] == "Sajangnim Teukje"

    scan = await session.scalar(select(Scan))
    assert scan.raw_ocr["items"][0]["raw_name"] == "김치 찌개"
    assert (Path(user) / scan.image_url).read_bytes() == JPEG


@pytest.mark.parametrize("data", [b"not an image", JPEG + b"0" * 1024 * 1024])
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


def test_llm_timeout_retries_once(client, user, monkeypatch):
    calls = []

    async def slow(image, mime):
        calls.append(1)
        raise TimeoutError

    monkeypatch.setattr(ocr, "_call", slow)
    r = upload(client)
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "LLM_TIMEOUT"
    assert len(calls) == 2


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
