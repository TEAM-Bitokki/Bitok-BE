"""저장한 메뉴"""

import base64
import binascii
import json
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import Dish
from app.common.schemas import Page
from app.core.errors import BitokError, ErrorCode
from app.domains.check.models import MyCheck
from app.domains.check.schemas import MyCheckItem, MyCheckListResponse
from app.domains.restaurant.models import Restaurant
from app.domains.scan.judge import judge, load_facts, load_rules
from app.domains.scan.models import Analysis, Scan


def _encode_cursor(saved_at: datetime, my_check_id: int) -> str:
    """마지막 항목의 정렬 값을 다음 요청에서 사용할 불투명한 문자열로 만든다."""
    payload = json.dumps(
        {"saved_at": saved_at.isoformat(), "id": my_check_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    """커서를 저장 시각과 my_checks.id로 복원하고 잘못된 값은 422로 처리한다."""
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        saved_at = datetime.fromisoformat(payload["saved_at"])
        my_check_id = payload["id"]
        if (
            saved_at.tzinfo is None
            or not isinstance(my_check_id, int)
            or isinstance(my_check_id, bool)
            or not 1 <= my_check_id <= 2**63 - 1  # BIGINT
        ):
            raise ValueError
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        raise BitokError(
            ErrorCode.VALIDATION_ERROR,
            "올바르지 않은 커서입니다.",
            status_code=422,
        ) from None
    return saved_at, my_check_id


async def list_my_checks(
    session: AsyncSession,
    user_id: int,
    cursor: str | None,
    limit: int,
) -> MyCheckListResponse:
    """현재 사용자가 저장한 메뉴를 최근 저장한 순서로 반환한다."""
    query = (
        select(MyCheck, Analysis, Dish, Restaurant)
        .join(Analysis, Analysis.id == MyCheck.analysis_id)
        .join(Dish, Dish.id == Analysis.dish_id)
        # 식당 없이 진행한 기록도 메뉴 목록에서 빠지지 않도록 OUTER JOIN한다.
        .outerjoin(Restaurant, Restaurant.id == MyCheck.restaurant_id)
        .where(MyCheck.user_id == user_id)
    )

    if cursor is not None:
        saved_at, my_check_id = _decode_cursor(cursor)
        # saved_at이 같은 행은 id를 두 번째 정렬 기준으로 사용해 중복, 누락을 막는다.
        query = query.where(
            or_(
                MyCheck.saved_at < saved_at,
                and_(
                    MyCheck.saved_at == saved_at,
                    MyCheck.id < my_check_id,
                ),
            )
        )

    rows = (
        await session.execute(
            query.order_by(MyCheck.saved_at.desc(), MyCheck.id.desc()).limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    visible = rows[:limit]

    # 빈 목록은 프로필 유무와 관계없이 정상적인 빈 페이지로 반환한다.
    if not visible:
        return MyCheckListResponse(
            items=[], page=Page(next_cursor=None, has_more=False)
        )

    rules = await load_rules(session, user_id)
    if rules is None:
        raise BitokError(
            ErrorCode.PROFILE_REQUIRED,
            "식이 프로필을 먼저 저장하세요.",
            status_code=422,
        )

    facts = await load_facts(session, *{a.scan_id for _, a, _, _ in visible})
    items = [
        MyCheckItem(
            my_check_id=my_check.id,
            analysis_id=analysis.id,
            dish_id=dish.id,
            name_ko=dish.name_ko,
            name_en=dish.name_en,
            restaurant_id=restaurant.id if restaurant else None,
            restaurant_name=restaurant.name if restaurant else None,
            # 저장 당시 verdict 대신 현재 프로필, 분석 스냅샷, 직원 답으로 다시 판정한다.
            verdict=judge(
                analysis.matches,
                rules,
                facts.get((analysis.scan_id, analysis.dish_id)),
            ),
            # 프론트가 이 값을 Today, Yesterday 또는 날짜로 표시한다.
            saved_at=my_check.saved_at.isoformat(),
        )
        for my_check, analysis, dish, restaurant in visible
    ]
    last_check = visible[-1][0]
    next_cursor = (
        _encode_cursor(last_check.saved_at, last_check.id) if has_more else None
    )
    return MyCheckListResponse(
        items=items,
        page=Page(next_cursor=next_cursor, has_more=has_more),
    )


async def save(session: AsyncSession, user_id: int, scan_id: int) -> None:
    """스캔의 메뉴를 전부 저장한다. 이미 저장한 메뉴는 그대로 둔다"""
    scan = await session.scalar(
        select(Scan)
        .where(Scan.id == scan_id, Scan.user_id == user_id)
        .with_for_update(read=True)  # 저장하는 동안 다시 분석하지 못하게
    )
    if scan is None:
        raise BitokError(ErrorCode.NOT_FOUND, "없는 스캔입니다.", 404)
    ids = list(
        await session.scalars(select(Analysis.id).where(Analysis.scan_id == scan_id))
    )
    if not ids:
        raise BitokError(ErrorCode.NOT_FOUND, "아직 분석하지 않은 스캔입니다.", 404)
    await session.execute(
        insert(MyCheck)
        .values(
            [
                {
                    "user_id": user_id,
                    "analysis_id": i,
                    "restaurant_id": scan.restaurant_id,
                }
                for i in ids
            ]
        )
        .on_conflict_do_nothing(constraint="uq_my_check")
    )
    await session.commit()
