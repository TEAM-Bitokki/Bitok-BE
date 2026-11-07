"""메뉴판 인식 (Gemini)"""

from typing import Literal

from google.genai import types
from pydantic import BaseModel, Field

from app.core import llm
from app.core.config import settings

SYSTEM = """<역할>
너는 한국 식당의 메뉴판 사진을 읽는다. 결과는 한국에 온 외국인이 알레르기와 식단을
확인하는 앱에서 쓰인다. 사용자는 네가 읽은 목록에서 먹을 메뉴를 고르고, 앱은 그 이름으로
재료를 찾는다.
</역할>

<원칙>
- 메뉴를 빠뜨리지 마라. 빠진 메뉴는 사용자가 확인할 기회를 잃는다.
- 사진에 없는 메뉴를 만들지 마라. 없는 메뉴는 엉뚱한 재료 정보로 이어진다.
- 글자가 흐리면 가장 그럴듯하게 읽되 confidence 를 낮게 준다.
</원칙>

<메뉴가 아닌 것>
가격, 영업시간, 전화번호, 가게 이름, 원산지 표시, 주차, 포장 안내,
"식사류" "주류" 같은 분류 제목, 메뉴 설명 문구
</메뉴가 아닌 것>

<예시>
메뉴판 글자: 불고기 15,000
결과: {"raw_name": "불고기", "name_en": "Bulgogi", "confidence": 0.95}

메뉴판 글자: 된장찌개 Soybean Paste Stew 8,000
결과: {"raw_name": "된장찌개", "name_en": "Doenjangjjigae", "confidence": 0.95}

메뉴판 글자: 김치찌개 (대) 12,000
결과: {"raw_name": "김치찌개 (대)", "name_en": "Kimchijjigae", "confidence": 0.9}

메뉴판 글자: 오징어순대 / 원산지: 오징어 국내산
결과: {"raw_name": "오징어순대", "name_en": "Ojingeosundae", "confidence": 0.9}

메뉴판 글자: (손글씨, 가운데가 번짐) 제?볶음
결과: {"raw_name": "제육볶음", "name_en": "Jeyukbokkeum", "confidence": 0.5}
</예시>"""

TASK = "이 메뉴판에 있는 메뉴를 모두 읽어라."


class MenuItem(BaseModel):
    raw_name: str = Field(
        description="메뉴판에 적힌 한국어 원문 그대로. 번역하거나 맞춤법을 고치지 않는다. "
        "직원에게 이 글자를 그대로 보여준다. 한국어와 영어가 함께 있으면 한국어 쪽"
    )
    name_en: str = Field(
        description="raw_name 의 한국어 발음을 로마자로 쓴 것. 메뉴명에서 띄어 쓴 곳만 띄우고 "
        "단어마다 첫 글자를 대문자로. 번역이 아니다 "
        "(불고기는 Bulgogi, Soy Sauce Beef 아님. 그린 샐러드는 Geurin Saelleodeu). "
        "(대) 같은 크기 표시는 뺀다"
    )
    confidence: float = Field(
        ge=0, le=1, description="이 이름을 맞게 읽었다고 확신하는 정도. 0~1"
    )


class MenuExtraction(BaseModel):
    items: list[MenuItem] = Field(description="메뉴판에 나온 순서대로")
    image_quality: Literal["ok", "blurry", "cropped"] = Field(
        description="사진 전체 상태. blurry 는 흐려서 읽기 어려움, cropped 는 메뉴판 일부가 잘림"
    )


MOCK = MenuExtraction(
    items=[
        MenuItem(raw_name="김치찌개", name_en="Kimchijjigae", confidence=0.96),
        MenuItem(raw_name="된장찌개", name_en="Doenjangjjigae", confidence=0.93),
        MenuItem(raw_name="제육볶음", name_en="Jeyukbokkeum", confidence=0.88),
    ],
    image_quality="ok",
)


async def extract_menu(image: bytes, mime: str) -> MenuExtraction:
    if settings.mock_llm:
        return MOCK
    return await llm.generate(
        SYSTEM,
        [types.Part.from_bytes(data=image, mime_type=mime), TASK],
        MenuExtraction,
    )
