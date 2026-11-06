from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, responses
from app.domains.ingredient.schemas import IngredientSearchResponse

router = APIRouter(prefix="/api/ingredients", tags=["성분"])


@router.get(
    "/search",
    response_model=IngredientSearchResponse,
    summary="알레르기 성분 검색",
    description=(
        "한국어와 영어를 모두 지원한다. 별칭도 검색된다.\n\n"
        "새우를 치면 새우, 새우젓이 각각 별개 항목으로 나온다. "
        "하나를 고른다고 나머지가 자동으로 선택되지 않고, 판정도 고른 성분만 한다.\n\n"
        "검색 노출 대상으로 표시된 성분만 돌려준다."
    ),
    responses=responses(UNAUTHORIZED),
)
async def search_ingredients(
    user_id: CurrentUser,
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
    raise NotImplementedError
