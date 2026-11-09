"""지도. 식당 등록, 주변 식당, 이름 검색"""

import logging
import math

import httpx
import pytest
from sqlalchemy import func, select

from app.core import kakao
from app.core.config import settings
from app.domains.restaurant.models import Restaurant
from app.domains.saved.models import SavedRestaurant

V4 = "550e8400-e29b-41d4-a716-446655440000"
H = {"deviceToken": V4}
LAT, LNG = 37.56, 126.98


def sign_up(client) -> int:
    return client.post("/api/auth/sign-up", json={"device_token": V4}).json()["user_id"]


def place(kakao_id, dlat, name=None) -> kakao.Place:
    return kakao.Place(kakao_id, name or kakao_id, "한식", "서울", LAT + dlat, LNG)


def fake(monkeypatch, name, places):
    calls = []

    async def search(*args):
        calls.append(args)
        return places

    monkeypatch.setattr(kakao, name, search)
    return calls


def body(kakao_id="1", name="명동교자"):
    return {"kakao_place_id": kakao_id, "name": name, "lat": LAT, "lng": LNG}


def kakao_pages(monkeypatch, *pages):
    """카카오가 쪽마다 이 문서들을 준다. 마지막 쪽이 끝"""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={"documents": pages[page - 1], "meta": {"is_end": page == len(pages)}},
        )

    monkeypatch.setattr(settings, "kakao_rest_api_key", "test-key")
    monkeypatch.setattr(kakao, "transport", httpx.MockTransport(handler))
    return seen


@pytest.mark.asyncio
async def test_resolve_saves_kakao_values_once(client, session, monkeypatch):
    """처음엔 카카오에서 다시 찾은 값으로 만든다. 이미 있으면 그 행을 돌려준다"""
    sign_up(client)
    seen = kakao_pages(monkeypatch, [doc(2), doc(1)])

    first = client.post("/api/restaurants", headers=H, json=body(name="가짜 이름"))
    again = client.post("/api/restaurants", headers=H, json=body(name="다른 이름"))

    assert first.status_code == again.status_code == 200
    assert first.json()["name"] == again.json()["name"] == "식당1"
    assert again.json()["restaurant_id"] == first.json()["restaurant_id"]
    assert [r.url.params["query"] for r in seen] == ["가짜 이름"]
    assert await session.scalar(select(func.count()).select_from(Restaurant)) == 1


@pytest.mark.asyncio
async def test_resolve_finds_place_on_later_page(client, session, monkeypatch):
    """같은 이름이 많아 두 번째 쪽에 있어도 찾는다. 찾으면 거기서 멈춘다"""
    sign_up(client)
    first_page = [doc(n) for n in range(100, 115)]
    seen = kakao_pages(monkeypatch, first_page, [doc(1)], [doc(200)])

    r = client.post("/api/restaurants", headers=H, json=body())

    assert r.status_code == 200
    assert r.json()["kakao_place_id"] == "1"
    assert [q.url.params["page"] for q in seen] == ["1", "2"]


@pytest.mark.asyncio
async def test_resolve_unknown_place_404(client, session, monkeypatch):
    sign_up(client)
    kakao_pages(monkeypatch, [doc(2)])

    r = client.post("/api/restaurants", headers=H, json=body())

    assert r.status_code == 404
    assert await session.scalar(select(func.count()).select_from(Restaurant)) == 0


@pytest.mark.asyncio
async def test_nearby_is_kakao_order_with_our_data(client, session, monkeypatch):
    """카카오 가까운 순 그대로, 우리 식당이면 id, 핀 숫자, 저장 여부를 붙인다. limit 을 지킨다"""
    user_id = sign_up(client)
    ours = Restaurant(kakao_place_id="b", name="b", lat=LAT + 0.002, lng=LNG)
    ours.checked_menu_count = 2
    elsewhere = Restaurant(kakao_place_id="c", name="c", lat=LAT + 0.0005, lng=LNG)
    elsewhere.checked_menu_count = 1  # 카카오 결과에 없으면 nearby 에는 안 나온다
    session.add_all([ours, elsewhere])
    await session.flush()
    session.add(SavedRestaurant(user_id=user_id, restaurant_id=ours.id))
    await session.commit()
    calls = fake(monkeypatch, "nearby", [place("a", 0.001), place("b", 0.002)])

    items = client.get(
        "/api/restaurants/nearby",
        headers=H,
        params={"lat": LAT, "lng": LNG, "radius_m": 1000, "limit": 2},
    ).json()["items"]

    assert calls == [(LAT, LNG, 1000, 2)]
    assert [
        (i["kakao_place_id"], i["checked_menu_count"], i["is_saved"]) for i in items
    ] == [("a", 0, False), ("b", 2, True)]
    assert (items[0]["restaurant_id"], items[0]["distance_m"]) == (None, 111)


@pytest.mark.asyncio
async def test_checked_only_restaurants_with_menus(client, session, monkeypatch):
    """지도 핀. 메뉴를 확인한 식당만 가까운 순으로, limit 까지. 카카오는 안 부른다"""
    user_id = sign_up(client)
    rows = [
        ("near", 0.0005, 1),
        ("mid", 0.001, 3),
        ("far", 0.002, 2),
        ("empty", 0.0003, 0),
        ("outside", 0.02, 5),
    ]
    session.add_all(
        Restaurant(kakao_place_id=k, name=k, lat=LAT + d, lng=LNG, checked_menu_count=n)
        for k, d, n in rows
    )
    await session.commit()
    mid = await session.scalar(
        select(Restaurant).where(Restaurant.kakao_place_id == "mid")
    )
    session.add(SavedRestaurant(user_id=user_id, restaurant_id=mid.id))
    await session.commit()
    calls = fake(monkeypatch, "nearby", [])

    items = client.get(
        "/api/restaurants/checked",
        headers=H,
        params={"lat": LAT, "lng": LNG, "radius_m": 1000, "limit": 2},
    ).json()["items"]

    assert calls == []
    assert [
        (i["kakao_place_id"], i["checked_menu_count"], i["is_saved"]) for i in items
    ] == [
        ("near", 1, False),
        ("mid", 3, True),
    ]


@pytest.mark.parametrize("meters,inside", [(999, True), (1000.4, False), (1001, False)])
@pytest.mark.asyncio
async def test_checked_radius_edge(client, session, meters, inside):
    """반경 경계 바로 안쪽 식당도 빠지지 않고, 0.4m 밖은 빠진다"""
    sign_up(client)
    dlat = math.degrees(meters / 6_371_000)
    session.add(
        Restaurant(
            kakao_place_id="edge",
            name="edge",
            lat=LAT + dlat,
            lng=LNG,
            checked_menu_count=1,
        )
    )
    await session.commit()

    items = client.get(
        "/api/restaurants/checked",
        headers=H,
        params={"lat": LAT, "lng": LNG, "radius_m": 1000},
    ).json()["items"]

    assert [i["kakao_place_id"] for i in items] == (["edge"] if inside else [])


@pytest.mark.asyncio
async def test_search_uses_location_when_given(client, session, monkeypatch):
    sign_up(client)
    calls = fake(monkeypatch, "keyword", [place("a", 0.001, "명동교자")])

    near = client.get(
        "/api/restaurants/search",
        headers=H,
        params={"q": " 명동교자 ", "lat": LAT, "lng": LNG},
    ).json()["items"]
    anywhere = client.get(
        "/api/restaurants/search", headers=H, params={"q": "명동교자"}
    ).json()["items"]

    assert calls == [("명동교자", LAT, LNG, 20), ("명동교자", None, None, 20)]
    assert (near[0]["distance_m"], anywhere[0]["distance_m"]) == (111, None)
    blank = client.get("/api/restaurants/search", headers=H, params={"q": "  "})
    assert blank.status_code == 422


def doc(n, category="음식점 > 한식 > 냉면", road="서울 중구 명동길 1"):
    return {
        "id": str(n),
        "place_name": f"식당{n}",
        "category_name": category,
        "road_address_name": road,
        "address_name": "서울 중구 명동2가 1",
        "x": "126.98",
        "y": "37.56",
    }


@pytest.mark.asyncio
async def test_kakao_client_pages_until_limit(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["page"])
        docs = [doc(n) for n in range(15)] if page == 1 else [doc(15, "음식점", "")]
        return httpx.Response(
            200, json={"documents": docs, "meta": {"is_end": page == 2}}
        )

    monkeypatch.setattr(settings, "kakao_rest_api_key", "test-key")
    monkeypatch.setattr(kakao, "transport", httpx.MockTransport(handler))

    found = await kakao.nearby(LAT, LNG, 1000, 50)  # 4쪽까지 가능해도 끝이면 멈춘다

    assert len(found) == 16
    assert (found[0].category, found[0].address) == ("한식", "서울 중구 명동길 1")
    assert (found[-1].category, found[-1].address) == (None, "서울 중구 명동2가 1")
    assert seen[0].headers["Authorization"] == "KakaoAK test-key"
    assert seen[0].url.params["category_group_code"] == "FD6"
    assert [r.url.params["page"] for r in seen] == ["1", "2"]


@pytest.mark.parametrize("key", ["test-key", ""])
@pytest.mark.asyncio
async def test_kakao_failure_is_502(client, session, monkeypatch, key):
    sign_up(client)
    monkeypatch.setattr(settings, "kakao_rest_api_key", key)
    monkeypatch.setattr(
        kakao, "transport", httpx.MockTransport(lambda _: httpx.Response(500))
    )

    r = client.get(
        "/api/restaurants/nearby", headers=H, params={"lat": LAT, "lng": LNG}
    )

    assert r.status_code == 502
    assert r.json()["error"]["code"] == "MAP_UNAVAILABLE"


@pytest.mark.asyncio
async def test_search_keeps_kakao_place(client, session, monkeypatch):
    """저장된 좌표가 카카오와 달라도 위치와 순서는 카카오 그대로, 우리 정보만 붙인다"""
    sign_up(client)
    session.add(
        Restaurant(
            kakao_place_id="b",
            name="b",
            lat=LAT + 0.0005,
            lng=LNG,
            checked_menu_count=2,
        )
    )
    await session.commit()
    fake(monkeypatch, "keyword", [place("a", 0.001), place("b", 0.002)])

    items = client.get(
        "/api/restaurants/search",
        headers=H,
        params={"q": "식당", "lat": LAT, "lng": LNG},
    ).json()["items"]

    assert [(i["kakao_place_id"], i["distance_m"]) for i in items] == [
        ("a", 111),
        ("b", 222),
    ]
    assert items[1]["restaurant_id"] is not None
    assert items[1]["checked_menu_count"] == 2


@pytest.mark.parametrize(
    "broken",
    [{"category_name": {"x": 1}}, {"y": "Infinity"}, {"y": "NaN"}, {"y": "91"}],
)
@pytest.mark.asyncio
async def test_kakao_bad_document_is_502(client, session, monkeypatch, broken):
    sign_up(client)
    kakao_pages(monkeypatch, [{**doc(1), **broken}])

    r = client.get("/api/restaurants/search", headers=H, params={"q": "식당"})

    assert r.status_code == 502


def test_kakao_urls_stay_out_of_logs(caplog):
    """httpx 요청 로그에서 카카오 것만 빠진다. 다른 요청 로그는 남는다"""
    caplog.set_level(logging.INFO, logger="httpx")
    log = logging.getLogger("httpx")

    for url in ("https://dapi.kakao.com/x?y=37.5", "https://example.com/v1"):
        log.info("HTTP Request: %s %s", "GET", httpx.URL(url))

    assert [r.getMessage() for r in caplog.records] == [
        "HTTP Request: GET https://example.com/v1"
    ]


@pytest.mark.parametrize(
    "here,there",
    [((0.0, 179.999), (0.0, -179.999)), ((0.0, -179.999), (0.0, 179.999))],
)
@pytest.mark.asyncio
async def test_checked_across_date_line(client, session, here, there):
    """날짜변경선 건너편 식당도 반경 안이면 나온다"""
    sign_up(client)
    session.add(
        Restaurant(
            kakao_place_id="x",
            name="x",
            lat=there[0],
            lng=there[1],
            checked_menu_count=1,
        )
    )
    await session.commit()

    items = client.get(
        "/api/restaurants/checked",
        headers=H,
        params={"lat": here[0], "lng": here[1], "radius_m": 1000},
    ).json()["items"]

    assert [i["kakao_place_id"] for i in items] == ["x"]
