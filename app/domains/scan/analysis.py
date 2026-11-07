"""메뉴 분석, 직원 질문"""

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.common.dictionary import GENERIC, tags_for, with_sources
from app.common.models import (
    Dish,
    DishAlias,
    DishIngredient,
    DishIngredientSource,
    Ingredient,
    IngredientAlias,
    canonical_name,
    dish_key,
    menu_name,
)
from app.common.schemas import Likelihood, MatchStatus, Verdict
from app.core.config import settings
from app.core.errors import BitokError, ErrorCode
from app.domains.profile.models import DietaryProfile
from app.domains.scan import judge as judging
from app.domains.scan.dish import DishInfo, describe_dishes
from app.domains.scan.judge import ASK_FOR_TAG, Rules, load_rules, status
from app.domains.scan.models import Analysis, Scan, StaffQuestion
from app.domains.scan.schemas import (
    AnalyzedMenu,
    AnalyzeRequest,
    AnalyzeResponse,
)
from app.domains.scan.schemas import StaffQuestion as QuestionOut

NAME_MAX = Ingredient.__table__.c.name_ko.type.length
ROMAN_MAX = Dish.__table__.c.name_en.type.length


async def analyze(
    session: AsyncSession, user_id: int, scan_id: int, body: AnalyzeRequest
) -> AnalyzeResponse:
    scan = await session.get(Scan, scan_id)
    if scan is None or scan.user_id != user_id:
        raise BitokError(ErrorCode.NOT_FOUND, "없는 스캔입니다.", 404)
    rules = await load_rules(session, user_id)
    if rules is None:
        raise BitokError(
            ErrorCode.PROFILE_REQUIRED, "식이 프로필을 먼저 저장하세요.", 422
        )

    names = {i.temp_id: menu_name(i.raw_name) for i in body.items}
    unknown = [t for t in body.selected_temp_ids if t not in names]
    if unknown:
        raise BitokError(
            ErrorCode.VALIDATION_ERROR,
            "목록에 없는 항목을 골랐습니다.",
            detail={"temp_ids": unknown},
        )
    menus = {}  # 같은 메뉴를 두 번 골라도 한 번만
    for t in body.selected_temp_ids:
        menus.setdefault(dish_key(names[t]), names[t])

    found = await _find_dishes(session, set(menus))
    ask = [n for k, n in menus.items() if k not in found or not found[k][1]]
    await session.rollback()  # 모델을 기다리는 동안 DB 커넥션을 잡고 있지 않게
    infos = await describe_dishes(ask)

    locked = (
        select(Scan.id)
        .where(Scan.id == scan_id, Scan.user_id == user_id)
        .with_for_update()
    )
    if await session.scalar(locked) is None:  # 같은 스캔을 두 번 보내면 차례로
        raise BitokError(ErrorCode.NOT_FOUND, "없는 스캔입니다.", 404)
    await session.execute(
        select(DietaryProfile.id)
        .where(DietaryProfile.user_id == user_id)
        .with_for_update(read=True)
    )
    rules = await load_rules(session, user_id)
    if rules is None:
        raise BitokError(
            ErrorCode.PROFILE_REQUIRED, "식이 프로필을 먼저 저장하세요.", 422
        )
    dish_ids = await _save_dishes(session, menus, found, infos)
    order = list(dict.fromkeys(dish_ids[k] for k in menus))
    dishes = {
        d.id: d for d in await session.scalars(select(Dish).where(Dish.id.in_(order)))
    }
    matches, possible = await _matches(session, order)
    asks = await _questions(session, order, dishes, matches, possible, rules)

    await session.execute(delete(StaffQuestion).where(StaffQuestion.scan_id == scan_id))
    await session.execute(delete(Analysis).where(Analysis.scan_id == scan_id))
    analyses = [
        Analysis(
            scan_id=scan_id,
            dish_id=d,
            verdict=judging.judge(matches[d], rules),
            matches=matches[d],
            asked_tags=[a[2] for a in asks if a[0] == d],
        )
        for d in order
    ]
    questions = [
        StaffQuestion(
            scan_id=scan_id,
            dish_id=d,
            ingredient_id=i,
            seq=n,
            question_ko=ko,
            question_en=en,
            target=target,
        )
        for n, (d, i, _, ko, en, target) in enumerate(asks, 1)
    ]
    session.add_all(analyses + questions)
    await session.commit()

    return AnalyzeResponse(
        menus=[
            AnalyzedMenu(
                dish_id=a.dish_id,
                name_ko=dishes[a.dish_id].name_ko,
                name_en=dishes[a.dish_id].name_en,
                analysis_id=a.id,
                verdict=a.verdict,
                matches=judging.flagged(a.matches, rules),
            )
            for a in analyses
        ],
        questions=[
            QuestionOut(
                question_id=q.id,
                seq=q.seq,
                total=len(questions),
                dish_id=q.dish_id,
                ingredient_id=q.ingredient_id,
                question_ko=q.question_ko,
                question_en=q.question_en,
            )
            for q in questions
        ],
    )


async def _find_dishes(
    session: AsyncSession, keys: set[str]
) -> dict[str, tuple[int, datetime | None]]:
    """정확히 같은 이름만 찾는다. 돼지김치찌개를 김치찌개로 합치지 않는다"""
    rows = await session.execute(
        select(Dish.canonical_name, Dish.id, Dish.described_at).where(
            Dish.canonical_name.in_(keys)
        )
    )
    found = {k: (i, at) for k, i, at in rows}
    rows = await session.execute(
        select(DishAlias.alias, Dish.id, Dish.described_at)
        .join(Dish, Dish.id == DishAlias.dish_id)
        .where(DishAlias.alias.in_(keys - set(found)))
    )
    return found | {k: (i, at) for k, i, at in rows}


async def _save_dishes(
    session: AsyncSession,
    menus: dict[str, str],
    found: dict[str, tuple[int, datetime | None]],
    infos: dict[str, DishInfo],
) -> dict[str, int]:
    now = datetime.now(UTC)
    new = {k: n for k, n in menus.items() if k not in found}
    if new:
        await session.execute(
            insert(Dish)
            .values(
                [
                    {
                        "canonical_name": k,
                        "name_ko": n,
                        "name_en": _roman(infos.get(n), n),
                        "described_at": now if n in infos else None,
                    }
                    for k, n in new.items()
                ]
            )
            .on_conflict_do_nothing(index_elements=["canonical_name"])
        )
        rows = await session.execute(
            select(Dish.canonical_name, Dish.id).where(
                Dish.canonical_name.in_(list(new))
            )
        )
        ids = dict(rows.all())
        found = found | {k: (ids[k], None) for k in new}
    dish_ids = {k: i for k, (i, _) in found.items()}

    described = {dish_ids[k]: n for k, n in menus.items() if n in infos}
    if described:
        await session.execute(
            update(Dish).where(Dish.id.in_(described)).values(described_at=now)
        )
    for k, n in menus.items():
        if n not in infos:
            continue
        # mock 으로 만들어 로마자 자리에 한국어가 들어간 메뉴만 채운다. 별칭 이름으로는 안 바꾼다
        await session.execute(
            update(Dish)
            .where(
                Dish.id == dish_ids[k],
                Dish.canonical_name == k,
                Dish.name_en == Dish.name_ko,
                Dish.verified.is_(False),
            )
            .values(name_en=_roman(infos[n], n))
        )
    said = [
        (dish_ids[k], _ingredients(infos[n]))
        for k, n in menus.items()
        if n in infos and not infos[n].unknown
    ]
    english = {name: en for _, i in said for name, (_, en, _) in i.items()}
    resolved = await _resolve(session, english)
    await _save_sources(session, said, resolved)
    merged = {}  # 별칭이 같은 재료(달걀, 계란)나 같은 메뉴의 두 이름은 합친다
    for dish_id, i in said:
        for name, (likelihood, _, _) in i.items():
            if name in resolved:
                key = (dish_id, resolved[name])
                if merged.get(key) != Likelihood.TYPICAL:
                    merged[key] = likelihood
    rows = [
        {"dish_id": d, "ingredient_id": i, "likelihood": lk, "source": "llm"}
        for (d, i), lk in merged.items()
    ]
    if rows:
        stmt = insert(DishIngredient).values(rows)
        # 먼저 possible 로 들어간 모델 답은 typical 로 올린다. 시드, 검수 데이터는 그대로
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_dish_ingredient",
                set_={"likelihood": stmt.excluded.likelihood},
                where=(DishIngredient.source == "llm")
                & (DishIngredient.likelihood == Likelihood.POSSIBLE)
                & (stmt.excluded.likelihood == Likelihood.TYPICAL),
            )
        )
    return dish_ids


def _roman(info: DishInfo | None, name: str) -> str:
    return ((info.name_en.strip() if info else "") or name)[:ROMAN_MAX]


def _ingredients(info: DishInfo) -> dict[str, tuple[Likelihood, str, list[str]]]:
    """이름 -> (likelihood, 영어, 원재료). 육수처럼 뭘로 만들었는지 모르는 건 possible"""
    result = {}
    for likelihood, items in (
        (Likelihood.POSSIBLE, info.possible),
        (Likelihood.TYPICAL, info.typical),
    ):
        for item in items:
            name = canonical_name(item.name_ko)
            before = result.get(name, (None, None, []))[2]
            contains = [canonical_name(c) for c in item.contains]
            result[name] = (
                Likelihood.POSSIBLE if name in GENERIC else likelihood,
                item.name_en.strip()[:NAME_MAX] or name,
                list(dict.fromkeys(before + contains)),
            )
    return result


async def _known(session: AsyncSession, names: list[str]) -> dict[str, int]:
    """이름이나 별칭으로 이미 있는 재료"""
    alias = dict(
        (
            await session.execute(
                select(IngredientAlias.alias, IngredientAlias.ingredient_id).where(
                    IngredientAlias.alias.in_(names)
                )
            )
        ).all()
    )
    ids = dict(
        (
            await session.execute(
                select(Ingredient.name_ko, Ingredient.id).where(
                    Ingredient.name_ko.in_(names)
                )
            )
        ).all()
    )
    return ids | alias


async def _resolve(session: AsyncSession, english: dict[str, str]) -> dict[str, int]:
    """별칭은 원래 이름으로 바꾸고, 사전에 없는 재료는 새로 넣는다"""
    names = list(english)
    alias = dict(
        (
            await session.execute(
                select(IngredientAlias.alias, IngredientAlias.ingredient_id).where(
                    IngredientAlias.alias.in_(names)
                )
            )
        ).all()
    )
    rest = [n for n in names if n not in alias]
    if rest:
        await session.execute(
            insert(Ingredient)
            .values(
                [
                    {
                        "name_ko": n,
                        "name_en": english[n],
                        "allergen_tags": sorted(tags_for(n)),
                        "searchable": False,
                    }
                    for n in rest
                ]
            )
            .on_conflict_do_nothing(index_elements=["name_ko"])
        )
    ids = dict(
        (
            await session.execute(
                select(Ingredient.name_ko, Ingredient.id).where(
                    Ingredient.name_ko.in_(rest)
                )
            )
        ).all()
    )
    return alias | ids


async def _save_sources(
    session: AsyncSession,
    said: list[tuple[int, dict[str, tuple[Likelihood, str, list[str]]]]],
    resolved: dict[str, int],
) -> None:
    """모델이 알려준 원재료는 그 메뉴에만 붙인다. 롤반죽에 계란이 드는지는 메뉴마다 다르다"""
    words = {c for _, i in said for (_, _, contains) in i.values() for c in contains}
    known = await _known(session, list(words))
    missing = sorted(words - set(known))
    if missing:
        await session.execute(
            insert(Ingredient)
            .values(
                [
                    {
                        "name_ko": c,
                        "name_en": c,
                        "allergen_tags": sorted(tags_for(c)),
                        "searchable": False,
                    }
                    for c in missing
                ]
            )
            .on_conflict_do_nothing(index_elements=["name_ko"])
        )
        known |= await _known(session, missing)
    rows = {
        (dish_id, resolved[name], known[c])
        for dish_id, i in said
        for name, (_, _, contains) in i.items()
        if name in resolved
        for c in contains
        if c in known and known[c] != resolved[name]
    }
    if rows:
        await session.execute(
            insert(DishIngredientSource)
            .values(
                [{"dish_id": d, "ingredient_id": i, "source_id": c} for d, i, c in rows]
            )
            .on_conflict_do_nothing()
        )


async def _told(
    session: AsyncSession, dish_ids: list[int]
) -> dict[int, dict[str, set[str]]]:
    """메뉴 -> 재료 이름 -> 모델이 알려준 원재료 이름"""
    source = aliased(Ingredient)
    rows = await session.execute(
        select(DishIngredientSource.dish_id, Ingredient.name_ko, source.name_ko)
        .join(Ingredient, Ingredient.id == DishIngredientSource.ingredient_id)
        .join(source, source.id == DishIngredientSource.source_id)
        .where(DishIngredientSource.dish_id.in_(dish_ids))
    )
    told = defaultdict(dict)
    for dish_id, name, base in rows:
        told[dish_id].setdefault(name, set()).add(base)
    return told


async def _matches(
    session: AsyncSession, dish_ids: list[int]
) -> tuple[dict[int, list[dict]], dict[int, list[str]]]:
    """메뉴마다 재료 전부와, 직원에게 물을 수 있는 possible 재료 이름

    저장된 재료에서 원재료를 따라간다. 두부가 있으면 대두도 나온다.
    """
    rows = (
        await session.execute(
            select(
                DishIngredient.dish_id,
                Ingredient.name_ko,
                DishIngredient.likelihood,
                DishIngredient.source,
            )
            .join(Ingredient, Ingredient.id == DishIngredient.ingredient_id)
            .where(DishIngredient.dish_id.in_(dish_ids))
            .order_by(Ingredient.name_ko)
        )
    ).all()
    told = await _told(session, dish_ids)
    names = list(
        set().union(*(with_sources({r.name_ko}, told[r.dish_id]) for r in rows))
    )
    info = {
        r.name_ko: r
        for r in await session.execute(
            select(
                Ingredient.id,
                Ingredient.name_ko,
                Ingredient.name_en,
                Ingredient.allergen_tags,
            ).where(Ingredient.name_ko.in_(names))
        )
    }

    matches = defaultdict(dict)
    possible = defaultdict(list)
    for r in rows:
        if r.likelihood == Likelihood.POSSIBLE:
            possible[r.dish_id].append(r.name_ko)
        for name in sorted(with_sources({r.name_ko}, told[r.dish_id])):
            if name not in info:
                continue
            m = matches[r.dish_id].setdefault(
                name,
                {
                    "ingredient_id": info[name].id,
                    "ingredient": name,
                    "ingredient_en": info[name].name_en,
                    "tags": sorted(info[name].allergen_tags),
                    "likelihood": r.likelihood,
                    "source": r.source,
                    "via": [],
                },
            )
            if r.likelihood == Likelihood.TYPICAL:
                m["likelihood"], m["source"] = r.likelihood, r.source
            m["via"].append(r.name_ko)
    return (
        {d: list(matches[d].values()) for d in dish_ids},
        {d: possible[d] for d in dish_ids},
    )


async def _questions(
    session: AsyncSession,
    order: list[int],
    dishes: dict[int, Dish],
    matches: dict[int, list[dict]],
    possible: dict[int, list[str]],
    rules: Rules,
) -> list[tuple[int, int, str, str, str]]:
    """(dish_id, ingredient_id, 물은 재료, 질문, 영어 질문, target). 알레르기를 먼저 묻는다"""
    allergens = await _allergens(session, rules)
    words = await _diet_words(session, rules)
    seen = {a.id for a in allergens}
    blind = [(0, a) for a in allergens] + [
        (1, w)
        for w in sorted(set(words.values()), key=lambda w: w.name_ko)
        if w.id not in seen
    ]
    asks = []
    for n, d in enumerate(order):
        if judging.judge(matches[d], rules) != Verdict.NOT_SURE:
            continue
        dish = dishes[d]
        if not matches[d]:
            asks += [
                (p, n, _ask(dish, w.id, w.name_ko, w.name_en, target=None))
                for p, w in blind
            ]
            continue
        by_name = {m["ingredient"]: m for m in matches[d]}
        for name in possible[d]:
            m = by_name.get(name)
            if m is None:
                continue
            if name in GENERIC:
                inside = [c for c in matches[d] if name in c["via"]]
                ids = {c["ingredient_id"] for c in inside}
                asks += [
                    (0, n, _ask_inside(dish, m, a))
                    for a in allergens
                    if set(a.allergen_tags) & set(m["tags"]) or a.id in ids
                ]
                tags = rules.blocked_tags & {t for c in inside for t in c["tags"]}
                for w in sorted(
                    {words[t] for t in tags & set(words)}, key=lambda w: w.name_ko
                ):
                    if w.name_ko == name:  # 비건에게 해산물은 들었는지만 물으면 된다
                        ask = _ask(dish, m["ingredient_id"], name, m["ingredient_en"])
                    else:
                        ask = _ask_inside(dish, m, w)
                    asks.append((1, n, ask))
                continue
            covers = [c for c in matches[d] if name in c["via"]]
            found = {status(c, rules) for c in covers} - {MatchStatus.ALLOWED}
            if found:
                p = 0 if MatchStatus.ALLERGY in found else 1
                asks.append(
                    (p, n, _ask(dish, m["ingredient_id"], name, m["ingredient_en"]))
                )
    asks.sort(key=lambda a: a[:2])
    return [a[2] for a in asks[: settings.max_staff_questions]]


def _ask(dish: Dish, ingredient_id: int, ko: str, en: str, target="same") -> tuple:
    """target 은 어느 재료에 대해 물었나. 메뉴 전체에 물었으면 None"""
    return (
        dish.id,
        ingredient_id,
        ko,
        f"이 {dish.name_ko}에 {ko}{_subject(ko)} 들어가나요?",
        f"Does this {dish.name_en} contain {en.lower()}?",
        ko if target == "same" else target,
    )


def _ask_inside(dish: Dish, generic: dict, inside) -> tuple:
    """육수, 해산물은 뭘로 만들었는지를 묻는다"""
    g, a = generic["ingredient"], inside.name_ko
    g_en, a_en = generic["ingredient_en"].lower(), inside.name_en.lower()
    return (
        dish.id,
        inside.id,
        a,
        f"이 {dish.name_ko}의 {g}에 {a}{_subject(a)} 들어가나요?",
        f"Does the {g_en} in this {dish.name_en} contain {a_en}?",
        g,
    )


async def _allergens(session: AsyncSession, rules: Rules) -> list:
    if not rules.allergen_ids:
        return []
    rows = await session.execute(
        select(
            Ingredient.id,
            Ingredient.name_ko,
            Ingredient.name_en,
            Ingredient.allergen_tags,
        )
        .where(Ingredient.id.in_(list(rules.allergen_ids)))
        .order_by(Ingredient.id)
    )
    return rows.all()


async def _diet_words(session: AsyncSession, rules: Rules) -> dict:
    """식단 분류 -> 직원에게 물을 말 (meat -> 고기). 재료를 모르는 메뉴와 육수에 쓴다"""
    words = {t: ASK_FOR_TAG[t] for t in rules.blocked_tags if t in ASK_FOR_TAG}
    if not words:
        return {}
    await session.execute(
        insert(Ingredient)
        .values(
            [
                {
                    "name_ko": ko,
                    "name_en": en,
                    "allergen_tags": sorted(tags_for(ko)),
                    "searchable": False,
                }
                for ko, en in set(words.values())
            ]
        )
        .on_conflict_do_nothing(index_elements=["name_ko"])
    )
    rows = await session.execute(
        select(Ingredient.id, Ingredient.name_ko, Ingredient.name_en).where(
            Ingredient.name_ko.in_([ko for ko, _ in words.values()])
        )
    )
    by_name = {r.name_ko: r for r in rows}
    return {t: by_name[ko] for t, (ko, _) in words.items()}


def _subject(word: str) -> str:
    """받침이 있으면 이, 없으면 가"""
    last = word[-1]
    if "가" <= last <= "힣":
        return "이" if (ord(last) - 0xAC00) % 28 else "가"
    return "이(가)"
