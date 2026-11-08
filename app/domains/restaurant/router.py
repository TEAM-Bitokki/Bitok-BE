from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.core import kakao
from app.core.db import SessionDep
from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, ErrorCode, responses
from app.domains.restaurant import places, service
from app.domains.restaurant.schemas import (
    MenuDetailResponse,
    PersonalizedMenuResponse,
    ResolveRestaurantRequest,
    RestaurantBrief,
    RestaurantDetailResponse,
    RestaurantListResponse,
)

router = APIRouter(prefix="/api/restaurants", tags=["식당"])

BigId = Annotated[int, Path(ge=1, le=2**63 - 1)]  # BIGINT
PROFILE = (422, "식이 프로필 미등록", ErrorCode.PROFILE_REQUIRED)
KAKAO = (502, "카카오 API 실패", ErrorCode.MAP_UNAVAILABLE)
Lat = Annotated[float, Query(ge=-90, le=90, examples=[37.5633])]
Lng = Annotated[float, Query(ge=-180, le=180, examples=[126.9855])]


@router.post(
    "",
    response_model=RestaurantBrief,
    summary="식당 등록 또는 조회",
    description=(
        "kakao_place_id` 로 우리 DB의 식당을 찾고, 없으면 만들어서 "
        "`restaurant_id` 를 돌려준다.\n\n"
        "nearby search 는 `restaurant_id` 가 null 일 수 있다. "
        "이미 있는 식당이면 바디 값은 무시하고 저장된 행을 돌려준다.\n\n"
        "처음 만들 때는 카카오에서 다시 찾아 카카오 값으로 저장한다. "
        "바디는 찾는 데만 쓴다."
    ),
    responses=responses(UNAUTHORIZED, (404, "카카오에 없는 식당"), KAKAO),
)
async def resolve(
    body: ResolveRestaurantRequest, user_id: CurrentUser, session: SessionDep
) -> RestaurantBrief:
    return await places.resolve(session, user_id, body)


@router.get(
    "/nearby",
    response_model=RestaurantListResponse,
    summary="주변 식당 조회",
    description=(
        "식당 고르기(Restaurant Select) 화면. 카카오 로컬 API 결과에 우리 데이터를 합쳐서 "
        "가까운 순으로 돌려준다. 최대 limit 개.\n\n"
        "restaurant_id 는 null 일 수 있다. (카카오에만 있고 우리 DB 에 아직) "
        "Select 를 누르면 `POST /api/restaurants` 로 id 를 받는다. "
        "식별은 항상 kakao_place_id 로 한다.\n\n"
        "Explore 지도 핀은 `GET /api/restaurants/checked` 를 쓴다."
    ),
    responses=responses(UNAUTHORIZED, KAKAO),
)
async def nearby(
    user_id: CurrentUser,
    session: SessionDep,
    lat: Lat,
    lng: Lng,
    radius_m: Annotated[int, Query(ge=100, le=5000)] = 1000,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RestaurantListResponse:
    return await places.nearby(session, user_id, lat, lng, radius_m, limit)


@router.get(
    "/checked",
    response_model=RestaurantListResponse,
    summary="메뉴를 확인한 식당 (지도 핀)",
    description=(
        "Explore 지도 화면. 반경 안에서 메뉴를 확인한 식당만 가까운 순으로 돌려준다. "
        "최대 limit 개.\n\n"
        "`checked_menu_count` 를 핀 숫자로 띄운다. 카카오를 부르지 않는다."
    ),
    responses=responses(UNAUTHORIZED),
)
async def checked(
    user_id: CurrentUser,
    session: SessionDep,
    lat: Lat,
    lng: Lng,
    radius_m: Annotated[int, Query(ge=100, le=20000)] = 3000,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RestaurantListResponse:
    return await places.checked(session, user_id, lat, lng, radius_m, limit)


@router.get(
    "/search",
    response_model=RestaurantListResponse,
    summary="식당 이름 검색",
    description="검색창에 입력했을 때. 현재 위치를 주면 거리순으로 정렬한다.",
    responses=responses(UNAUTHORIZED, KAKAO),
)
async def search(
    user_id: CurrentUser,
    session: SessionDep,
    q: Annotated[
        str,
        Query(min_length=1, max_length=100, pattern=r"\S", examples=["맛있는식당"]),
    ],
    lat: Lat | None = None,
    lng: Lng | None = None,
    limit: Annotated[int, Query(ge=1, le=kakao.MAX)] = 20,
) -> RestaurantListResponse:
    return await places.search(session, user_id, q.strip(), lat, lng, limit)


@router.get(
    "/{restaurant_id}",
    response_model=RestaurantDetailResponse,
    summary="식당 상세",
    description=(
        "Checked Menu 를 무한 스크롤로 내려준다. "
        "모든 사용자가 분석한 메뉴이고, 저장 여부와 무관하다.\n\n"
        "메뉴 단위로 묶어서 내려준다. 같은 메뉴를 여러 명이 분석했어도 한 줄이고, "
        "가장 최근에 분석한 순이다.\n\n"
        "현재 위치(lat, lng)를 주면 `distance_m` 을 채운다."
    ),
    responses=responses(
        UNAUTHORIZED,
        (404, "없는 식당"),
        (422, "잘못된 커서", ErrorCode.VALIDATION_ERROR),
    ),
)
async def detail(
    restaurant_id: BigId,
    user_id: CurrentUser,
    session: SessionDep,
    cursor: Annotated[str | None, Query(description="이전 응답의 next_cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    lat: Lat | None = None,
    lng: Lng | None = None,
) -> RestaurantDetailResponse:
    return await service.detail(
        session, user_id, restaurant_id, cursor, limit, lat, lng
    )


@router.get(
    "/{restaurant_id}/menus/for-you",
    response_model=PersonalizedMenuResponse,
    summary="내 프로필 기준 메뉴 목록",
    description=(
        "식당 상세의 **For you**. 같은 식당이라도 사용자마다 다른 결과가 나온다.\n\n"
        "같은 메뉴는 하나로 합친다. 재료는 가장 최근 분석, "
        "직원 답은 최근 6개월 모든 사용자의 답을 모아 가장 위험한 쪽을 따른다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 식당"), PROFILE),
)
async def for_you(
    restaurant_id: BigId,
    user_id: CurrentUser,
    session: SessionDep,
) -> PersonalizedMenuResponse:
    return await service.for_you(session, user_id, restaurant_id)


@router.get(
    "/{restaurant_id}/menus/{dish_id}",
    response_model=MenuDetailResponse,
    summary="메뉴 상세",
    description=(
        "Menu Detail 화면. 판정과 걸린 성분, 일반적인 재료를 보여준다.\n\n"
        "여러 사람이 다르게 답했으면 가장 위험한 쪽을 따른다.\n\n"
        "- 하나라도 들어가요 -> Avoid\n"
        "- 없고 모르겠어요가 있으면 -> Not Sure\n"
        "- 전부 안 들어가요 -> Safe\n\n"
        "최근 6개월 기록만 집계한다. 레시피 변경과 오래된 오답을 함께 걸러낸다.\n\n"
        "`matches[].verdict` 로 Avoid, Not Sure 칸을 나눈다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 식당 또는 메뉴"), PROFILE),
)
async def menu_detail(
    restaurant_id: BigId,
    dish_id: BigId,
    user_id: CurrentUser,
    session: SessionDep,
) -> MenuDetailResponse:
    return await service.menu_detail(session, user_id, restaurant_id, dish_id)
