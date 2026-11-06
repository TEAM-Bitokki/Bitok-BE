"""AI Hub 관광 음식메뉴판 라벨로 성분·음식 사전 채우기"""

import asyncio
import json
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cache

from sqlalchemy import delete, select, update
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
INGREDIENT_MAX = Ingredient.__table__.c.name_ko.type.length
DISH_MAX = Dish.__table__.c.name_ko.type.length

# 기획서 알레르기 검색 목록. 이것만 검색에 나온다
SEARCHABLE = [
    ("우유", "Milk", []),
    ("유제품", "Dairy", []),
    ("치즈", "Cheese", []),
    ("버터", "Butter", []),
    ("크림", "Cream", []),
    ("요거트", "Yogurt", ["요구르트"]),
    ("계란", "Egg", ["달걀", "난류"]),
    ("달걀흰자", "Egg White", ["흰자"]),
    ("달걀노른자", "Egg Yolk", ["노른자"]),
    ("메추리알", "Quail Egg", []),
    ("오리알", "Duck Egg", []),
    ("밀", "Wheat", []),
    ("글루텐", "Gluten", []),
    ("메밀", "Buckwheat", []),
    ("보리", "Barley", []),
    ("귀리", "Oats", []),
    ("옥수수", "Corn", []),
    ("쌀", "Rice", []),
    ("대두", "Soy", ["콩"]),
    ("땅콩", "Peanut", []),
    ("완두콩", "Pea", []),
    ("병아리콩", "Chickpea", []),
    ("렌틸콩", "Lentil", []),
    ("강낭콩", "Kidney Bean", []),
    ("녹두", "Mung Bean", []),
    ("팥", "Red Bean", []),
    ("견과류", "Tree Nuts", ["견과"]),
    ("아몬드", "Almond", []),
    ("호두", "Walnut", []),
    ("캐슈너트", "Cashew", ["캐슈넛"]),
    ("피스타치오", "Pistachio", []),
    ("헤이즐넛", "Hazelnut", []),
    ("마카다미아", "Macadamia", []),
    ("잣", "Pine Nut", []),
    ("밤", "Chestnut", []),
    ("코코넛", "Coconut", []),
    ("참깨", "Sesame", ["깨"]),
    ("들깨", "Perilla Seed", []),
    ("겨자", "Mustard", []),
    ("생선", "Fish", []),
    ("멸치", "Anchovy", []),
    ("고등어", "Mackerel", []),
    ("연어", "Salmon", []),
    ("참치", "Tuna", []),
    ("대구", "Cod", []),
    ("정어리", "Sardine", []),
    ("장어", "Eel", []),
    ("명태", "Pollock", []),
    ("가다랑어", "Bonito", []),
    ("생선알", "Fish Roe", []),
    ("갑각류", "Crustacean Shellfish", []),
    ("새우", "Shrimp", []),
    ("대하", "Prawn", []),
    ("게", "Crab", []),
    ("바닷가재", "Lobster", ["랍스터"]),
    ("가재", "Crayfish", []),
    ("새우젓", "Salted Shrimp", []),
    ("연체동물", "Molluscs", []),
    ("조개류", "Shellfish", []),
    ("조개", "Clam", []),
    ("홍합", "Mussel", []),
    ("굴", "Oyster", []),
    ("가리비", "Scallop", []),
    ("전복", "Abalone", []),
    ("오징어", "Squid", []),
    ("문어", "Octopus", []),
    ("쇠고기", "Beef", ["소고기"]),
    ("돼지고기", "Pork", []),
    ("닭고기", "Chicken", []),
    ("오리고기", "Duck", ["오리"]),
    ("양고기", "Lamb", []),
    ("염소고기", "Goat", []),
    ("복숭아", "Peach", []),
    ("토마토", "Tomato", []),
    ("키위", "Kiwi", []),
    ("바나나", "Banana", []),
    ("아보카도", "Avocado", []),
    ("딸기", "Strawberry", []),
    ("사과", "Apple", []),
    ("망고", "Mango", []),
    ("파인애플", "Pineapple", []),
    ("감귤류", "Citrus", []),
    ("셀러리", "Celery", ["샐러리"]),
    ("양파", "Onion", []),
    ("마늘", "Garlic", []),
    ("버섯", "Mushroom", []),
    ("아황산류", "Sulphites", ["아황산함유"]),
    ("젤라틴", "Gelatin", []),
    ("코코아", "Cocoa", []),
    ("초콜릿", "Chocolate", ["초코"]),
    ("꿀", "Honey", []),
]

SYNONYMS = {"야채": "채소", "해물": "해산물"}

# 메뉴에 왼쪽이 있으면 오른쪽 재료도 붙인다. 두부가 든 마파밥이 대두에 걸리게
# fmt: off
SOURCES = {
    "대두": (
        "두부", "순두부", "연두부", "건두부", "된장", "간장", "소이소스", "춘장",
        "청국장", "콩비지", "유부", "유부주머니", "낫또", "낫토", "두반장", "콩나물",
        "검은콩", "데리야끼소스",
    ),
    "밀": (
        "밀가루", "밀떡", "빵", "빵가루", "바게트", "또띠아", "튀김가루", "파스타면",
        "스파게티면", "쫄면", "소면", "생면", "수제비", "꽃빵", "간장", "춘장",
        "데리야끼소스",
    ),
    "글루텐": ("밀", "보리"),
    "메밀": ("메밀면", "메밀가루"),
    "쌀": (
        "쌀면", "쌀떡", "쌀가루", "찹쌀", "찹쌀가루", "현미", "흑미", "누룽지", "떡",
        "가래떡",
    ),
    "옥수수": ("스위트콘", "옥수수면"),
    "우유": ("유제품",),
    "땅콩": ("땅콩버터", "땅콩소스"),
    "아몬드": ("아몬드가루",),
    "코코넛": ("코코넛밀크",),
    "견과류": (
        "아몬드", "호두", "캐슈너트", "피스타치오", "헤이즐넛", "마카다미아", "잣", "밤",
    ),
    "참깨": ("참기름", "흑임자", "참깨소스"),
    "들깨": ("들기름", "들깻가루"),
    "겨자": ("겨자소스", "머스타드소스", "허니머스타드소스"),
    "멸치": ("멸치육수", "멸치액젓"),
    "참치": ("참다랑어", "눈다랑어"),
    "명태": (
        "동태", "생태", "황태", "북어", "코다리", "노가리", "먹태", "명란", "명란젓",
        "명태알", "동태알",
    ),
    "가다랑어": ("가쓰오부시",),
    "생선알": ("날치알", "명란", "명란젓", "연어알", "대구알", "명태알", "동태알"),
    "새우": ("새우젓", "대하"),
    "게": ("꽃게", "대게", "홍게", "게살", "게장", "게내장", "킹크랩", "크랩"),
    "가재": ("민물가재",),
    "조개": ("바지락", "백합조개", "모시조개", "키조개", "꼬막", "재첩"),
    "조개류": ("조개", "홍합", "굴", "가리비"),
    "굴": ("굴소스",),
    "가리비": ("관자", "참가리비"),
    "전복": ("전복내장",),
    "오징어": ("갑오징어", "한치", "꼴뚜기"),
    "문어": ("낙지", "주꾸미", "쭈꾸미"),
    "쇠고기": (
        "소갈비", "차돌박이", "양지", "양지머리", "소꼬리", "소머리", "소뼈", "소양",
        "소염통", "소힘줄", "소도가니", "사골", "꽃등심", "채끝살", "안창살", "치마살",
        "부채살", "토시살", "소내장", "소특수부위", "특양", "천엽",
    ),
    "돼지고기": (
        "삼겹살", "오겹살", "대패삼겹살", "목살", "항정살", "가브리살", "갈매기살",
        "베이컨", "햄", "소시지", "소세지", "독일식소시지", "스팸", "족발", "순대",
        "하몽", "살라미", "페퍼로니", "페페로니", "차슈", "오돌뼈",
    ),
    "오리고기": ("훈제오리", "오리훈제"),
    "버섯": ("트러플", "트러플오일"),
    "감귤류": ("레몬", "라임", "오렌지", "자몽", "유자", "한라봉", "귤", "감귤"),
    "코코아": ("코코아파우더", "초콜릿"),
}
# fmt: on

SOURCE_WORDS = {
    "닭": "닭고기",
    "돼지": "돼지고기",
    "새우": "새우",
    "오징어": "오징어",
    "치즈": "치즈",
    "참치": "참치",
    "연어": "연어",
    "대구": "대구",
    "장어": "장어",
    "토마토": "토마토",
    "마늘": "마늘",
    "양파": "양파",
    "버섯": "버섯",
}

GENERIC = {"육수", "해산물"}  # 뭘로 만들었는지 몰라서 possible 로 넣는다

TAG_SOURCES = {
    "fish": ("생선",),
    "crustacean": ("갑각류",),
    "shellfish": ("연체동물",),
    "dairy": ("유제품",),
    "egg": ("계란",),
    "honey": ("꿀",),
}

# fmt: off
TAG_WORDS = {
    "meat": (
        "고기", "돼지", "갈비", "삼겹", "오겹", "목살", "항정", "가브리살", "갈매기살",
        "늑간살", "부채살", "안창살", "채끝살", "치마살", "토시살", "차돌", "등심",
        "안심", "양지", "도가니", "소꼬리", "소머리", "소뼈", "소양", "소염통",
        "소힘줄", "특수부위", "특양", "천엽", "오돌뼈", "곱창", "막창", "대창",
        "순대", "족발", "소내장", "선지", "편육", "사골", "장조림", "완자", "베이컨",
        "햄", "하몽", "살라미", "페퍼로니", "페페로니", "소시지", "소세지", "스팸",
        "차슈", "닭", "오리고기", "훈제오리", "오리훈제", "메추리", "육포", "번데기",
    ),
    "fish": (
        "생선", "활어", "회무침", "연어", "참치", "다랑어", "고등어", "갈치", "꽁치",
        "삼치", "명태", "동태", "생태", "황태", "북어", "먹태", "노가리", "코다리",
        "대구", "광어", "도다리", "우럭", "노래미", "도미", "방어", "민어",
        "병어", "숭어", "농어", "전어", "조기", "굴비", "임연수", "점성어", "빙어",
        "메기", "메로", "미꾸라지", "장어", "멸치", "액젓", "어묵", "연육", "크래미",
        "가쓰오", "홍어", "간재미", "가오리", "가자미", "청어", "아귀", "아구",
        "복어", "도치", "쥐치", "쥐포", "상어", "샥스핀", "황새치", "정어리",
        "날치알", "명란", "생선알", "연어알",
    ),
    "crustacean": (
        "새우", "킹크랩", "크랩", "랍스터", "가재", "게살", "게장", "게내장",
    ),
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
    "유제품": {"dairy"},
    "갑각류": {"crustacean"},
    "대하": {"crustacean"},
    "연체동물": {"shellfish"},
    "조개류": {"shellfish"},
    "오리알": {"egg"},
    "젤라틴": {"meat"},
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
    "내장": {"meat"},
    "가라아게": {"meat"},
    "햄버거빵": set(),
    "땅콩버터": set(),
}


def tags_for(name: str) -> set[str]:
    if name in TAG_EXACT:
        return set(TAG_EXACT[name])
    tags = {tag for tag, words in TAG_WORDS.items() if any(w in name for w in words)}
    if name.endswith("게") and not name.endswith(("멍게", "성게")):  # 꽃게 홍게
        tags.add("crustacean")
    if name.endswith("돔"):  # 참돔 옥돔. 돔베고기는 아님
        tags.add("fish")
    return tags


DERIVED = defaultdict(set)
for base, names in SOURCES.items():
    for name in names:
        DERIVED[name].add(base)


@cache
def sources_of(name: str) -> frozenset[str]:
    found = set(DERIVED.get(name, ()))
    found |= {base for word, base in SOURCE_WORDS.items() if word in name}
    if name not in GENERIC:
        for tag in tags_for(name):
            found |= set(TAG_SOURCES.get(tag, ()))
    found.discard(name)
    return frozenset(found)


def with_sources(names: set[str]) -> set[str]:
    """두부 → 대두, 연어 → 생선. 원재료를 끝까지 따라간다"""
    found, todo = set(names), list(names)
    while todo:
        for base in sources_of(todo.pop()) - found:
            found.add(base)
            todo.append(base)
    return found


def roman(text: str) -> str:
    """kimchijjigae → Kimchijjigae"""
    text = text.strip()
    return text[:1].upper() + text[1:]


@dataclass
class Plan:
    ingredients: dict[str, tuple[str, set[str], bool]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    dishes: dict[str, tuple[str, str, set[str]]] = field(default_factory=dict)
    too_long: list[str] = field(default_factory=list)


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
    for ko, en, aliases in SEARCHABLE:
        plan.ingredients[ko] = (en, tags_for(ko), True)
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
        if max(len(name), len(en)) > INGREDIENT_MAX:
            plan.too_long.append(name)
            continue
        plan.ingredients[name] = (en, tags_for(name), False)
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
            names = {resolve(i) for i in row["ingredients.ko"] + row["allergy"]}
            seen.update(n for n in with_sources(names) if n in plan.ingredients)
        typical = {n for n, c in seen.items() if c / len(group) >= TYPICAL_RATIO}
        name_ko = Counter(r["ko"].strip() for r in group).most_common(1)[0][0]
        name_en = roman(Counter(r["en.ROMAN"] for r in group).most_common(1)[0][0])
        if max(len(key), len(name_ko), len(name_en)) > DISH_MAX:
            plan.too_long.append(key)
            continue
        plan.dishes[key] = (name_ko, name_en, typical)
    return plan


async def seed(session: AsyncSession, plan: Plan) -> dict[str, int]:
    """다시 돌리면 성분·별칭·public_data 연결을 지금 규칙대로 맞춘다"""
    stmt = insert(Ingredient).values(
        [
            {"name_ko": ko, "name_en": en, "allergen_tags": sorted(t), "searchable": s}
            for ko, (en, t, s) in plan.ingredients.items()
        ]
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["name_ko"],
            set_={
                "name_en": stmt.excluded.name_en,
                "allergen_tags": stmt.excluded.allergen_tags,
                "searchable": stmt.excluded.searchable,
            },
        )
    )
    listed = [ko for ko, (_, _, s) in plan.ingredients.items() if s]
    await session.execute(
        update(Ingredient)
        .where(Ingredient.name_ko.not_in(listed))
        .values(searchable=False)
    )
    ids = dict((await session.execute(select(Ingredient.name_ko, Ingredient.id))).all())

    await session.execute(
        delete(IngredientAlias).where(IngredientAlias.alias.not_in(list(plan.aliases)))
    )
    if plan.aliases:
        stmt = insert(IngredientAlias).values(
            [{"alias": a, "ingredient_id": ids[ko]} for a, ko in plan.aliases.items()]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["alias"],
                set_={"ingredient_id": stmt.excluded.ingredient_id},
            )
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
    await session.execute(
        delete(DishIngredient).where(
            DishIngredient.source == "public_data",
            DishIngredient.dish_id.in_([dish_ids[k] for k in plan.dishes]),
        )
    )

    links = [
        {
            "dish_id": dish_ids[k],
            "ingredient_id": ids[n],
            "likelihood": "possible" if n in GENERIC else "typical",
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
        "too_long": plan.too_long,
    }


async def main(paths: list[str]) -> None:
    plan = build(read_rows(paths))
    async with SessionMaker() as session:
        print(await seed(session, plan))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('python -m scripts.seed_dictionary "VL_01. 서울.zip" [...]')
    asyncio.run(main(sys.argv[1:]))
