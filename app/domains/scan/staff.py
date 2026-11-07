"""직원 응답, 최종 결과"""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import Dish
from app.core.errors import BitokError, ErrorCode
from app.domains.restaurant.models import Restaurant
from app.domains.scan import judge as judging
from app.domains.scan.judge import load_facts, load_rules
from app.domains.scan.models import Analysis, Scan, StaffCheck, StaffQuestion
from app.domains.scan.schemas import AnalyzedMenu, AnswerRequest, ResultResponse


async def answer(
    session: AsyncSession, user_id: int, scan_id: int, body: AnswerRequest
) -> None:
    """같은 질문에 다시 답하면 마지막 답으로 바꾼다"""
    row = (
        await session.execute(
            select(StaffQuestion, Scan.restaurant_id)
            .join(Scan, Scan.id == StaffQuestion.scan_id)
            .where(
                StaffQuestion.id == body.question_id,
                StaffQuestion.scan_id == scan_id,
                Scan.user_id == user_id,
            )
            .with_for_update(of=StaffQuestion)
        )
    ).first()
    if row is None:
        raise BitokError(ErrorCode.NOT_FOUND, "없는 질문입니다.", 404)
    question, restaurant_id = row
    now = datetime.now(UTC)
    question.answer, question.answered_at = body.answer, now

    if restaurant_id is not None:
        if question.staff_check_id is None:
            check = StaffCheck(
                restaurant_id=restaurant_id,
                dish_id=question.dish_id,
                ingredient_id=question.ingredient_id,
                target=question.target,
                user_id=user_id,
                answer=body.answer,
            )
            session.add(check)
            await session.flush()
            question.staff_check_id = check.id
        else:
            await session.execute(
                update(StaffCheck)
                .where(StaffCheck.id == question.staff_check_id)
                .values(answer=body.answer, checked_at=now)
            )
    await session.commit()


async def result(session: AsyncSession, user_id: int, scan_id: int) -> ResultResponse:
    scan = await session.get(Scan, scan_id)
    if scan is None or scan.user_id != user_id:
        raise BitokError(ErrorCode.NOT_FOUND, "없는 스캔입니다.", 404)
    rules = await load_rules(session, user_id)
    if rules is None:
        raise BitokError(
            ErrorCode.PROFILE_REQUIRED, "식이 프로필을 먼저 저장하세요.", 422
        )
    rows = (
        await session.execute(
            select(Analysis, Dish)
            .join(Dish, Dish.id == Analysis.dish_id)
            .where(Analysis.scan_id == scan_id)
            .order_by(Analysis.id)
        )
    ).all()
    if not rows:
        raise BitokError(ErrorCode.NOT_FOUND, "아직 분석하지 않은 스캔입니다.", 404)

    facts = await load_facts(session, scan_id)
    restaurant = (
        await session.get(Restaurant, scan.restaurant_id)
        if scan.restaurant_id
        else None
    )
    checked_at = await session.scalar(
        select(func.max(StaffQuestion.answered_at)).where(
            StaffQuestion.scan_id == scan_id
        )
    )
    return ResultResponse(
        scan_id=scan.id,
        restaurant_id=scan.restaurant_id,
        restaurant_name=restaurant.name if restaurant else None,
        menus=[
            AnalyzedMenu(
                dish_id=d.id,
                name_ko=d.name_ko,
                name_en=d.name_en,
                analysis_id=a.id,
                verdict=judging.judge(a.matches, rules, facts.get((scan_id, d.id))),
                matches=judging.flagged(a.matches, rules, facts.get((scan_id, d.id))),
            )
            for a, d in rows
        ],
        checked_at=(checked_at or scan.created_at).isoformat(),
    )
