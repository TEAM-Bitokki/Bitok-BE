"""카카오 로컬 API. 음식점(FD6)만 찾는다"""

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.errors import BitokError, ErrorCode

log = logging.getLogger(__name__)

BASE = "https://dapi.kakao.com/v2/local/search"
FOOD = "FD6"
PAGE = 15  # 카카오가 한 번에 주는 최대
MAX = 50  # 검색 API 의 limit 상한과 같다
TIMEOUT = 5
transport: httpx.AsyncBaseTransport | None = None  # 테스트에서 바꾼다


@dataclass(frozen=True)
class Place:
    kakao_place_id: str
    name: str
    category: str | None
    address: str | None
    lat: float
    lng: float


class _HideKakaoUrl(logging.Filter):
    """httpx 요청 로그 중 카카오 것만 뺀다. URL 에 사용자 위치가 있다"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not any("dapi.kakao.com" in str(a) for a in record.args or ())


logging.getLogger("httpx").addFilter(_HideKakaoUrl())


def _place(doc: dict) -> Place:
    """category_name 은 "음식점 > 한식 > 냉면". 두 번째 칸만 쓴다"""
    parts = [p.strip() for p in (doc.get("category_name") or "").split(">")]
    lat, lng = float(doc["y"]), float(doc["x"])
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("좌표가 올바르지 않다")
    return Place(
        kakao_place_id=doc["id"],
        name=doc["place_name"],
        category=parts[1] if len(parts) > 1 else None,
        address=doc.get("road_address_name") or doc.get("address_name") or None,
        lat=lat,
        lng=lng,
    )


def _unavailable() -> BitokError:
    return BitokError(
        ErrorCode.MAP_UNAVAILABLE, "지도 서비스가 응답하지 않습니다.", status_code=502
    )


async def _search(
    kind: str, params: dict, limit: int, until: str | None = None
) -> list[Place]:
    """until 은 찾으면 멈출 장소 id"""
    if not settings.kakao_rest_api_key:
        log.warning("KAKAO_REST_API_KEY 가 비어 있다")
        raise _unavailable()
    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_api_key}"}
    places: dict[str, Place] = {}
    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=TIMEOUT, transport=transport
        ) as client:
            for page in range(1, (limit - 1) // PAGE + 2):
                r = await client.get(
                    f"{BASE}/{kind}.json",
                    params={
                        **params,
                        "category_group_code": FOOD,
                        "page": page,
                        "size": PAGE,
                    },
                )
                r.raise_for_status()
                body = r.json()
                for doc in body["documents"]:
                    p = _place(doc)
                    places.setdefault(p.kakao_place_id, p)
                if body["meta"]["is_end"] or until in places:
                    break
    except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as e:
        status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else ""
        # 요청 URL 에 사용자 위치가 있어서 예외를 통째로 남기지 않는다
        log.warning("카카오 로컬 API 실패: %s %s", type(e).__name__, status)
        raise _unavailable() from None
    return list(places.values())[:limit]


async def nearby(lat: float, lng: float, radius_m: int, limit: int) -> list[Place]:
    """가까운 순"""
    params = {"x": lng, "y": lat, "radius": radius_m, "sort": "distance"}
    return await _search("category", params, limit)


async def find(place_id: str, name: str, lat: float, lng: float) -> Place | None:
    """이름으로 다시 찾아 이 장소가 있는지. 검색 결과에 나올 수 있는 만큼 본다"""
    params = {"query": name, "x": lng, "y": lat, "sort": "distance"}
    found = await _search("keyword", params, MAX, until=place_id)
    return next((p for p in found if p.kakao_place_id == place_id), None)


async def keyword(
    query: str, lat: float | None, lng: float | None, limit: int
) -> list[Place]:
    """위치를 주면 가까운 순, 아니면 정확도순"""
    params = {"query": query}
    if lat is not None and lng is not None:
        params |= {"x": lng, "y": lat, "sort": "distance"}
    return await _search("keyword", params, limit)
