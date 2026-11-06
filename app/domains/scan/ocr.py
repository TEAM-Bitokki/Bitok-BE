"""메뉴판 인식 (Gemini)"""

import asyncio
from functools import cache
from typing import Literal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.core.errors import BitokError, ErrorCode

PROMPT = """메뉴판 사진에서 음식과 음료의 메뉴 이름만 읽어라.

- 사진에 없는 메뉴를 만들지 마라
- raw_name 은 메뉴판에 적힌 한국어 원문 그대로. 번역하지 마라
- name_en 은 로마자 표기다. 번역이 아니다 (불고기 → Bulgogi, Soy Sauce Beef 아님)
- 가격, 영업시간, 주차 안내, 가게 이름처럼 메뉴가 아닌 글자는 빼라
- confidence 는 그 이름을 제대로 읽었다고 확신하는 정도 (0~1)
- image_quality 는 사진 전체 상태. ok, blurry(흐림), cropped(잘림) 중 하나
"""


class MenuItem(BaseModel):
    raw_name: str
    name_en: str
    confidence: float = Field(ge=0, le=1)


class MenuExtraction(BaseModel):
    items: list[MenuItem]
    image_quality: Literal["ok", "blurry", "cropped"]


MOCK = MenuExtraction(
    items=[
        MenuItem(raw_name="김치찌개", name_en="Kimchijjigae", confidence=0.96),
        MenuItem(raw_name="된장찌개", name_en="Doenjangjjigae", confidence=0.93),
        MenuItem(raw_name="제육볶음", name_en="Jeyukbokkeum", confidence=0.88),
    ],
    image_quality="ok",
)


@cache
def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


async def _call(image: bytes, mime: str) -> MenuExtraction:
    r = await _client().aio.models.generate_content(
        model=settings.gemini_model,
        contents=[types.Part.from_bytes(data=image, mime_type=mime), PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MenuExtraction,
            temperature=0,
        ),
    )
    if not r.text:
        raise ValueError("빈 응답")
    return MenuExtraction.model_validate_json(r.text)


async def extract_menu(image: bytes, mime: str) -> MenuExtraction:
    """실패하면 바로 한 번만 다시 부른다. 사용자가 화면에서 기다리는 중이다"""
    if settings.mock_llm:
        return MOCK
    for _ in range(2):
        try:
            return await asyncio.wait_for(_call(image, mime), settings.llm_timeout_sec)
        except TimeoutError:
            error = BitokError(
                ErrorCode.LLM_TIMEOUT, "모델 응답이 너무 늦습니다.", status_code=504
            )
        except (errors.APIError, ValidationError, ValueError):
            error = BitokError(
                ErrorCode.LLM_UNAVAILABLE, "모델이 응답하지 않습니다.", status_code=502
            )
    raise error
