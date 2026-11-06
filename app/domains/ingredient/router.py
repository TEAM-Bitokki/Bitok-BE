from typing import Annotated

from fastapi import APIRouter, Query

from app.core.db import SessionDep
from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, responses
from app.domains.ingredient import service
from app.domains.ingredient.schemas import IngredientSearchResponse

router = APIRouter(prefix="/api/ingredients", tags=["성분"])


@router.get(
    "/search",
    response_model=IngredientSearchResponse,
    summary="알레르기 성분 검색",
    description=(
        "기획서의 알레르기 목록 91개만 나온다.\n\n"
        "한국어, 영어, 별칭 (달걀 -> 계란)으로 검색된다. "
        "생선, 갑각류, 견과류처럼 분류 이름도 항목으로 있다.\n\n"
        "이름이 같은 것 -> 앞이 같은 것 -> 포함하는 것 순서. "
        "별칭으로 찾았으면 `matched_alias` 에 그 별칭이 온다."
    ),
    responses=responses(UNAUTHORIZED),
)
async def search_ingredients(
    user_id: CurrentUser,
    session: SessionDep,
    q: Annotated[
        str,
        Query(
            min_length=1,
            description="검색어(한국어 영어)",
            examples=["새우"],
        ),
    ],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> IngredientSearchResponse:
    return IngredientSearchResponse(items=await service.search(session, q, limit))
