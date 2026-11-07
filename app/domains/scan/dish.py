"""음식 재료 묻기 (Gemini) - 사용자 정보는 보내지 않는다."""

import asyncio

from pydantic import BaseModel, Field

from app.common.dictionary import SEARCHABLE
from app.common.models import Ingredient, canonical_name
from app.core import llm
from app.core.config import settings

BATCH = 10
NAME_MAX = Ingredient.__table__.c.name_ko.type.length

SYSTEM = f"""<역할>
너는 한국 음식의 재료를 안다. 결과는 한국에 온 외국인이 알레르기와 식단을 확인하는 앱에서
쓰인다. 앱은 네가 준 재료로 메뉴를 걸러내고, 확실하지 않은 재료는 식당 직원에게 물어본다.
</역할>

<원칙>
- typical 은 그 음식이라면 거의 어느 가게든 들어가는 재료다.
- possible 은 가게에 따라 들어가거나, 들어가도 눈에 잘 안 보이는 재료다.
  젓갈, 액젓, 육수, 굴소스, 참기름, 버터, 우유, 계란물, 밀가루 반죽처럼 숨은 재료를 빠뜨리지 마라.
  빠진 재료는 알레르기가 있는 사람이 확인할 기회를 잃는다.
- 재료는 메뉴에 실제로 들어가는 모습 그대로 쓴다. 햄은 햄, 빵은 빵, 마요네즈는 마요네즈다.
- 같은 재료를 가리키면 아래 단어로 쓴다 (소고기는 쇠고기, 달걀은 계란).
  해당하는 단어가 없으면 흔히 쓰는 짧은 일반 명사로 쓴다.
- 재료마다 contains 에 그 재료를 만드는 원재료를 아래 단어에서 골라 넣는다.
  치킨텐더는 닭고기, 밀, 계란이고 마요네즈는 계란이다. 재료 자체가 아래 단어면 비운다.
  아몬드우유는 아몬드, 두유는 대두다. 이름에 우유가 있다고 우유를 넣지 마라.
- unknown 은 이름만으로 어떤 종류의 음식인지조차 알 수 없을 때만 true 다.
  "사장님특제" "오늘의메뉴" "A세트" 같은 이름이다. 이때는 재료를 지어내지 마라.
  "핫스파이시 샐러드"처럼 종류를 알면 그 종류에 흔한 재료를 typical 에,
  가게마다 다를 재료를 possible 에 넣는다.
</원칙>

<재료 단어>
{", ".join(ko for ko, _, _ in SEARCHABLE)}
</재료 단어>

<예시>
메뉴: 김치찌개
결과: {{"name": "김치찌개", "name_en": "Kimchijjigae", "unknown": false,
"typical": [{{"name_ko": "김치", "name_en": "Kimchi", "contains": []}},
{{"name_ko": "돼지고기", "name_en": "Pork", "contains": []}},
{{"name_ko": "두부", "name_en": "Tofu", "contains": ["대두"]}},
{{"name_ko": "대파", "name_en": "Green Onion", "contains": []}}],
"possible": [{{"name_ko": "새우젓", "name_en": "Salted Shrimp", "contains": ["새우"]}},
{{"name_ko": "멸치액젓", "name_en": "Anchovy Sauce", "contains": ["멸치"]}},
{{"name_ko": "멸치육수", "name_en": "Anchovy Broth", "contains": ["멸치"]}}]}}

메뉴: 치킨텐더 샌드위치
결과: {{"name": "치킨텐더 샌드위치", "name_en": "Chikintendeo Saendeuwichi", "unknown": false,
"typical": [{{"name_ko": "빵", "name_en": "Bread", "contains": ["밀"]}},
{{"name_ko": "치킨텐더", "name_en": "Chicken Tender", "contains": ["닭고기", "밀", "계란"]}},
{{"name_ko": "양상추", "name_en": "Lettuce", "contains": []}}],
"possible": [{{"name_ko": "마요네즈", "name_en": "Mayonnaise", "contains": ["계란"]}},
{{"name_ko": "치즈", "name_en": "Cheese", "contains": ["우유"]}},
{{"name_ko": "머스타드소스", "name_en": "Mustard Sauce", "contains": ["겨자"]}}]}}

메뉴: 사장님특제
결과: {{"name": "사장님특제", "name_en": "Sajangnimteukje", "unknown": true, "typical": [], "possible": []}}
</예시>"""


class IngredientName(BaseModel):
    name_ko: str = Field(description="한국어 재료 이름 하나. 띄어쓰지 않는다")
    name_en: str = Field(
        description="재료의 영어 이름. 이건 번역이다 (새우젓은 Salted Shrimp)"
    )
    contains: list[str] = Field(
        default_factory=list,
        description="이 재료를 만드는 원재료. <재료 단어> 에서만 고른다. 없으면 빈 목록",
    )


class DishInfo(BaseModel):
    name: str = Field(description="받은 메뉴명 그대로")
    name_en: str = Field(
        description="메뉴명의 한국어 발음을 로마자로 쓴 것. 메뉴명에서 띄어 쓴 곳만 띄우고 "
        "단어마다 첫 글자를 대문자로. 번역이 아니다 (불고기는 Bulgogi)"
    )
    unknown: bool = Field(description="이름만으로 무슨 음식인지 알 수 없으면 true")
    typical: list[IngredientName] = Field(description="거의 항상 들어가는 재료")
    possible: list[IngredientName] = Field(
        description="가게에 따라 들어갈 수 있거나 눈에 잘 안 보이는 재료"
    )


class DishInfos(BaseModel):
    dishes: list[DishInfo] = Field(description="받은 메뉴 순서대로 하나씩")


async def _describe(names: list[str]) -> dict[str, DishInfo]:
    def check(result: DishInfos) -> None:
        # 띄어쓰기는 모델이 고쳐서 돌려줄 수 있다
        if [canonical_name(d.name) for d in result.dishes] != [
            canonical_name(n) for n in names
        ]:
            raise ValueError("받은 메뉴와 답한 메뉴가 다르다")
        for d in result.dishes:
            for i in d.typical + d.possible:
                for name in [i.name_ko, *i.contains]:
                    if not 0 < len(canonical_name(name)) <= NAME_MAX:
                        raise ValueError(f"재료 이름을 저장할 수 없다: {name!r}")

    task = "이 메뉴들의 재료를 알려줘.\n" + "\n".join(f"메뉴: {n}" for n in names)
    result = await llm.generate(SYSTEM, [task], DishInfos, check)
    return dict(zip(names, result.dishes, strict=True))


async def describe_dishes(names: list[str]) -> dict[str, DishInfo]:
    """mock_llm 이면 비워서 돌려준다. 새 메뉴는 재료를 모르는 메뉴가 된다"""
    if settings.mock_llm or not names:
        return {}
    batches = [names[i : i + BATCH] for i in range(0, len(names), BATCH)]
    results = await asyncio.gather(*(_describe(b) for b in batches))
    return {name: info for infos in results for name, info in infos.items()}
