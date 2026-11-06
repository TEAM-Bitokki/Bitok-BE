"""AI Hub 관광 음식메뉴판 라벨로 성분·음식 사전 채우기"""

import asyncio
import json
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import (
    Dish,
    DishIngredient,
    Ingredient,
    IngredientAlias,
    canonical_name,
)
from app.core.db import SessionMaker

MIN_INGREDIENT = 3  # 이보다 적게 나온 재료는 버린다
MIN_DISH = 2  # 이보다 적게 나온 메뉴는 런타임 LLM 몫
TYPICAL_RATIO = 0.5  # 같은 메뉴 중 이 비율 이상에 나온 재료만 typical

# 식약처 알레르기 유발물질 22종
ALLERGENS = [
    ("계란", "Egg", {"egg"}, ["달걀", "난류"]),
    ("우유", "Milk", {"dairy"}, []),
    ("메밀", "Buckwheat", set(), []),
    ("땅콩", "Peanut", set(), []),
    ("대두", "Soybean", set(), ["콩"]),
    ("밀", "Wheat", set(), []),
    ("고등어", "Mackerel", {"fish"}, []),
    ("게", "Crab", {"crustacean"}, []),
    ("새우", "Shrimp", {"crustacean"}, []),
    ("돼지고기", "Pork", {"meat"}, []),
    ("복숭아", "Peach", set(), []),
    ("토마토", "Tomato", set(), []),
    ("아황산류", "Sulfites", set(), ["아황산함유"]),
    ("호두", "Walnut", set(), []),
    ("닭고기", "Chicken", {"meat"}, []),
    ("쇠고기", "Beef", {"meat"}, ["소고기"]),
    ("오징어", "Squid", {"shellfish"}, []),
    ("조개", "Clam", {"shellfish"}, []),
    ("굴", "Oyster", {"shellfish"}, []),
    ("전복", "Abalone", {"shellfish"}, []),
    ("홍합", "Mussel", {"shellfish"}, []),
    ("잣", "Pine Nut", set(), []),
]

SYNONYMS = {"야채": "채소", "해물": "해산물"}
ALLERGY_ONLY = {"밀가루": "밀"}  # allergy 칸에 잘못 들어온 표기

# fmt: off
UNSEARCHABLE = {
    "채소", "고기", "해산물", "육수", "음료", "알코올", "탄산", "탄산수",
    "면", "얼음", "과일", "소스", "양념", "비빔장",
}

TAG_WORDS = {
    "meat": (
        "고기", "돼지", "갈비", "삼겹", "오겹", "목살", "항정", "가브리살", "갈매기살",
        "늑간살", "부채살", "안창살", "채끝살", "치마살", "토시살", "차돌", "등심",
        "안심", "양지", "도가니", "소꼬리", "소머리", "소뼈", "소양", "소염통",
        "소힘줄", "특수부위", "특양", "천엽", "오돌뼈", "곱창", "막창", "대창",
        "순대", "족발", "내장", "선지", "편육", "사골", "장조림", "완자", "베이컨",
        "햄", "하몽", "살라미", "페퍼로니", "페페로니", "소시지", "소세지", "스팸",
        "차슈", "닭", "오리고기", "훈제오리", "오리훈제", "메추리", "육포", "번데기",
    ),
    "fish": (
        "생선", "활어", "회무침", "연어", "참치", "다랑어", "고등어", "갈치", "꽁치",
        "삼치", "명태", "동태", "생태", "황태", "북어", "먹태", "노가리", "코다리",
        "대구", "광어", "도다리", "우럭", "노래미", "도미", "돔", "방어", "민어",
        "병어", "숭어", "농어", "전어", "조기", "굴비", "임연수", "점성어", "빙어",
        "메기", "메로", "미꾸라지", "장어", "멸치", "액젓", "어묵", "연육", "크래미",
        "가쓰오", "홍어", "간재미", "가오리", "가자미", "청어", "아귀", "아구",
        "복어", "도치", "쥐치", "쥐포", "상어", "샥스핀", "황새치",
        "날치알", "명란", "생선알", "연어알",
    ),
    "crustacean": ("새우", "킹크랩", "크랩", "랍스터", "가재", "게살", "게장"),
    "shellfish": (
        "조개", "굴", "전복", "홍합", "바지락", "가리비", "소라", "골뱅이", "다슬기",
        "올갱이", "우렁", "오징어", "낙지", "문어", "주꾸미", "쭈꾸미", "꼴뚜기",
        "한치", "해삼", "멍게", "성게", "미더덕", "개불", "해파리", "관자", "꼬막",
        "재첩",
    ),
    "dairy": (
        "우유", "치즈", "버터", "크림", "요거트", "요구르트", "연유", "분유",
        "모짜렐라", "리코타", "파마산", "마스카포네", "라떼",
    ),
    "egg": ("계란", "달걀", "마요네즈", "노른자", "흰자", "지단", "수란"),
    "honey": ("꿀", "허니"),
}
# fmt: on

TAG_EXACT = {
    "육수": {"meat", "fish"},
    "해산물": {"fish", "shellfish", "crustacean"},
    "알": {"fish"},
    "오리": {"meat"},
    "서대": {"fish"},
    "굴비": {"fish"},
    "참치특수부위": {"fish"},
    "게맛살": {"fish"},
    "맛살": {"fish"},
    "멍게": {"shellfish"},
    "성게": {"shellfish"},
    "보리굴비": {"fish"},
    "전복내장": {"shellfish"},
    "메추리알": {"egg"},
    "땅콩버터": set(),
}


def tags_for(name: str) -> set[str]:
    if name in TAG_EXACT:
        return set(TAG_EXACT[name])
    tags = {tag for tag, words in TAG_WORDS.items() if any(w in name for w in words)}
    if name.endswith("게"):  # 꽃게 대게 홍게
        tags.add("crustacean")
    return tags


def roman(text: str) -> str:
    """kimchijjigae → Kimchijjigae"""
    text = text.strip()
    return text[:1].upper() + text[1:]


@dataclass
class Plan:
    ingredients: dict[str, tuple[str, set[str], bool]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    dishes: dict[str, tuple[str, str, set[str]]] = field(default_factory=dict)


def read_rows(paths: list[str]) -> list[dict]:
    rows = []
    for path in paths:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith(".json"):
                    doc = json.loads(z.read(name).decode("utf-8-sig"))
                    rows += [a["menu_information"] for a in doc.get("annotations", [])]
    return rows


def build(rows: list[dict]) -> Plan:
    plan = Plan()
    alias_of = {}
    for ko, en, tags, aliases in ALLERGENS:
        plan.ingredients[ko] = (en, tags, True)
        for alias in aliases:
            alias_of[alias] = ko
    alias_of |= SYNONYMS

    def resolve(name: str) -> str:
        name = name.strip()
        return alias_of.get(name, name)

    count, en_of = Counter(), defaultdict(Counter)
    for row in rows:
        ko, en = row["ingredients.ko"], row["ingredients.en"]
        for i, name in enumerate(ko):
            name = resolve(name)
            count[name] += 1
            if len(en) == len(ko):
                en_of[name][en[i]] += 1

    for name, n in count.items():
        if name in plan.ingredients or n < MIN_INGREDIENT or not en_of[name]:
            continue
        en = en_of[name].most_common(1)[0][0]
        plan.ingredients[name] = (en, tags_for(name), name not in UNSEARCHABLE)
    plan.aliases = {a: ko for a, ko in alias_of.items() if ko in plan.ingredients}

    groups = defaultdict(list)
    for row in rows:
        if row["food_type"] == "음료/주류" or not (
            row["ingredients.ko"] or row["allergy"]
        ):
            continue
        groups[canonical_name(row["ko"])].append(row)

    for key, group in groups.items():
        if len(group) < MIN_DISH or not key:
            continue
        seen = Counter()
        for row in group:
            names = {resolve(i) for i in row["ingredients.ko"]}
            names |= {resolve(ALLERGY_ONLY.get(a, a)) for a in row["allergy"]}
            seen.update(n for n in names if n in plan.ingredients)
        typical = {n for n, c in seen.items() if c / len(group) >= TYPICAL_RATIO}
        name_ko = Counter(r["ko"].strip() for r in group).most_common(1)[0][0]
        name_en = roman(Counter(r["en.ROMAN"] for r in group).most_common(1)[0][0])
        plan.dishes[key] = (name_ko, name_en, typical)
    return plan


async def seed(session: AsyncSession, plan: Plan) -> dict[str, int]:
    """여러 번 돌려도 중복이 생기지 않는다. 성분은 최신 규칙으로 갱신한다"""
    existing = {i.name_ko: i for i in await session.scalars(select(Ingredient))}
    for ko, (en, tags, searchable) in plan.ingredients.items():
        row = existing.get(ko) or Ingredient(name_ko=ko)
        row.name_en, row.allergen_tags, row.searchable = en, sorted(tags), searchable
        session.add(row)
        existing[ko] = row
    await session.flush()
    ids = {ko: row.id for ko, row in existing.items()}

    if plan.aliases:
        await session.execute(
            insert(IngredientAlias)
            .values(
                [
                    {"alias": a, "ingredient_id": ids[ko]}
                    for a, ko in plan.aliases.items()
                ]
            )
            .on_conflict_do_nothing(index_elements=["alias"])
        )

    if plan.dishes:
        await session.execute(
            insert(Dish)
            .values(
                [
                    {"canonical_name": k, "name_ko": ko, "name_en": en}
                    for k, (ko, en, _) in plan.dishes.items()
                ]
            )
            .on_conflict_do_nothing(index_elements=["canonical_name"])
        )
    dish_ids = dict((await session.execute(select(Dish.canonical_name, Dish.id))).all())

    links = [
        {
            "dish_id": dish_ids[k],
            "ingredient_id": ids[n],
            "likelihood": "typical",
            "source": "public_data",
        }
        for k, (_, _, names) in plan.dishes.items()
        for n in names
    ]
    for start in range(0, len(links), 5000):
        await session.execute(
            insert(DishIngredient)
            .values(links[start : start + 5000])
            .on_conflict_do_nothing(constraint="uq_dish_ingredient")
        )
    await session.commit()

    return {
        "ingredients": len(plan.ingredients),
        "aliases": len(plan.aliases),
        "dishes": len(plan.dishes),
        "dish_ingredients": len(links),
    }


async def main(paths: list[str]) -> None:
    plan = build(read_rows(paths))
    async with SessionMaker() as session:
        print(await seed(session, plan))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('python -m scripts.seed_dictionary "VL_01. 서울.zip" [...]')
    asyncio.run(main(sys.argv[1:]))
