"""음식 재료 묻기 (Gemini) - 사용자 정보는 보내지 않는다."""

import asyncio

from pydantic import BaseModel, Field

from app.common.dictionary import SEARCHABLE
from app.core import llm
from app.core.config import settings

BATCH = 10

SYSTEM = f"""<역할>
너는 한국 음식의 재료를 안다. 결과는 한국에 온 외국인이 알레르기와 식단을 확인하는 앱에서
쓰인다. 앱은 네가 준 재료로 메뉴를 걸러내고, 확실하지 않은 재료는 식당 직원에게 물어본다.
</역할>

<원칙>
- typical 은 그 음식이라면 거의 어느 가게든 들어가는 재료다.
- possible 은 가게에 따라 들어가거나, 들어가도 눈에 잘 안 보이는 재료다.
  젓갈, 액젓, 육수, 굴소스, 참기름, 버터, 우유, 계란물, 밀가루 반죽처럼 숨은 재료를 빠뜨리지 마라.
  빠진 재료는 알레르기가 있는 사람이 확인할 기회를 잃는다.
- 이름만으로 무슨 음식인지 알 수 없으면 unknown 을 true 로 하고 재료를 지어내지 마라.
  "사장님특제" "오늘의메뉴" "A세트" 같은 이름이다.
- 재료 이름은 아래 단어에 해당하면 그 단어를 그대로 쓴다. 해당하지 않으면 흔히 쓰는 짧은 일반 명사로 쓴다.
</원칙>

<재료 단어>
{", ".join(ko for ko, _, _ in SEARCHABLE)}
</재료 단어>

<예시>
메뉴: 김치찌개
결과: {{"name": "김치찌개", "name_en": "Kimchijjigae", "unknown": false,
"typical": [{{"name_ko": "김치", "name_en": "Kimchi"}}, {{"name_ko": "돼지고기", "name_en": "Pork"}},
{{"name_ko": "두부", "name_en": "Tofu"}}, {{"name_ko": "대파", "name_en": "Green Onion"}}],
"possible": [{{"name_ko": "새우젓", "name_en": "Salted Shrimp"}},
{{"name_ko": "멸치액젓", "name_en": "Anchovy Sauce"}}, {{"name_ko": "멸치육수", "name_en": "Anchovy Broth"}}]}}

메뉴: 사장님특제
결과: {{"name": "사장님특제", "name_en": "Sajangnimteukje", "unknown": true, "typical": [], "possible": []}}
</예시>"""


class IngredientName(BaseModel):
    name_ko: str = Field(description="한국어 재료 이름 하나. 띄어쓰지 않는다")
    name_en: str = Field(
        description="재료의 영어 이름. 이건 번역이다 (새우젓은 Salted Shrimp)"
    )


class DishInfo(BaseModel):
    name: str = Field(description="받은 메뉴명 그대로")
    name_en: str = Field(
        description="메뉴명의 한국어 발음을 로마자로 붙여 쓴 것. 첫 글자만 대문자. "
        "번역이 아니다 (불고기는 Bulgogi)"
    )
    unknown: bool = Field(description="이름만으로 무슨 음식인지 알 수 없으면 true")
    typical: list[IngredientName] = Field(description="거의 항상 들어가는 재료")
    possible: list[IngredientName] = Field(
        description="가게에 따라 들어갈 수 있거나 눈에 잘 안 보이는 재료"
    )


class DishInfos(BaseModel):
    dishes: list[DishInfo] = Field(description="받은 메뉴 순서대로 하나씩")


async def _describe(names: list[str]) -> list[DishInfo]:
    def check(result: DishInfos) -> None:
        if [d.name for d in result.dishes] != names:
            raise ValueError("받은 메뉴와 답한 메뉴가 다르다")

    task = "이 메뉴들의 재료를 알려줘.\n" + "\n".join(f"메뉴: {n}" for n in names)
    result = await llm.generate(SYSTEM, [task], DishInfos, check)
    return result.dishes


async def describe_dishes(names: list[str]) -> dict[str, DishInfo]:
    """mock_llm 이면 비워서 돌려준다. 새 메뉴는 재료를 모르는 메뉴가 된다"""
    if settings.mock_llm or not names:
        return {}
    batches = [names[i : i + BATCH] for i in range(0, len(names), BATCH)]
    results = await asyncio.gather(*(_describe(b) for b in batches))
    return {info.name: info for infos in results for info in infos}
