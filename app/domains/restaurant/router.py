from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, ErrorCode, responses
from app.domains.restaurant.schemas import (
    MenuDetailResponse,
    PersonalizedMenuResponse,
    RestaurantDetailResponse,
    RestaurantListResponse,
)

router = APIRouter(prefix="/api/restaurants", tags=["식당"])


@router.get(
    "/nearby",
    response_model=RestaurantListResponse,
    summary="주변 식당 조회",
    description=(
        "카카오 로컬 API 결과에 우리 데이터를 합쳐서 돌려준다. "
        "거리순이다.\n\n"
        "checked_menu_count>0 이면 지도 핀에 숫자를 띄운다."
    ),
    responses=responses(
        UNAUTHORIZED, (502, "카카오 API 실패", ErrorCode.MAP_UNAVAILABLE)
    ),
)
async def nearby(
    user_id: CurrentUser,
    lat: Annotated[float, Query(examples=[37.5633])],
    lng: Annotated[float, Query(examples=[126.9855])],
    radius_m: Annotated[int, Query(ge=100, le=5000)] = 1000,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RestaurantListResponse:
    raise NotImplementedError


@router.get(
    "/search",
    response_model=RestaurantListResponse,
    summary="식당 이름 검색",
    description="검색창에 입력했을 때. 현재 위치를 주면 거리순으로 정렬한다.",
    responses=responses(
        UNAUTHORIZED, (502, "카카오 API 실패", ErrorCode.MAP_UNAVAILABLE)
    ),
)
async def search(
    user_id: CurrentUser,
    q: Annotated[str, Query(min_length=1, examples=["맛있는식당"])],
    lat: Annotated[float | None, Query()] = None,
    lng: Annotated[float | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RestaurantListResponse:
    raise NotImplementedError


@router.get(
    "/{restaurant_id}",
    response_model=RestaurantDetailResponse,
    summary="식당 상세",
    description=(
        "Checked Menu 를 무한 스크롤로 내려준다. "
        "모든 사용자가 분석한 메뉴이고, 저장 여부와 무관하다.\n\n"
        "메뉴 단위로 묶어서 내려준다. 같은 메뉴를 여러 명이 분석했어도 한 줄이고, "
        "가장 최근에 확인된 순이다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 식당")),
)
async def detail(
    restaurant_id: int,
    user_id: CurrentUser,
    cursor: Annotated[str | None, Query(description="이전 응답의 next_cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RestaurantDetailResponse:
    raise NotImplementedError


@router.get(
    "/{restaurant_id}/menus/for-you",
    response_model=PersonalizedMenuResponse,
    summary="내 프로필 기준 메뉴 목록",
    description=(
        "식당 상세의 **For you**. 같은 식당이라도 사용자마다 다른 결과가 나온다.\n\n"
        "같은 메뉴가 여러 번 분석됐으면 하나로 합쳐서 보여준다. "
        "판정은 가장 위험한 쪽을 따른다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 식당")),
)
async def for_you(
    restaurant_id: int,
    user_id: CurrentUser,
) -> PersonalizedMenuResponse:
    raise NotImplementedError


@router.get(
    "/{restaurant_id}/menus/{dish_id}",
    response_model=MenuDetailResponse,
    summary="메뉴 상세",
    description=(
        "Menu Detail 화면. 판정과 걸린 성분, 일반적인 재료를 보여준다.\n\n"
        " 여러 사람이 다르게 답했으면 "
        "가장 위험한 쪽을 따른다.\n\n"
        "- 하나라도 들어가요 → Avoid\n"
        "- 없고 모르겠어요 가 있으면 → Not Sure\n"
        "- 전부 안 들어가요 → Safe\n\n"
        "최근 6개월 기록만 집계한다. 레시피 변경과 오래된 오답을 함께 걸러낸다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 식당 또는 메뉴")),
)
async def menu_detail(
    restaurant_id: int,
    dish_id: int,
    user_id: CurrentUser,
) -> MenuDetailResponse:
    raise NotImplementedError
