"""Gemini 호출"""

import asyncio
import logging
from collections.abc import Callable
from functools import cache

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.errors import BitokError, ErrorCode

log = logging.getLogger(__name__)


@cache
def _client() -> genai.Client:
    # SDK 는 안에서 최대 5번 다시 부른다. 재시도는 generate 가 정한다
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1)
        ),
    )


async def _call[T: BaseModel](
    model: str, system: str, contents: list, schema: type[T]
) -> T:
    # Gemini 3.6 이상은 temperature 를 무시하고, 넣으면 앞으로 400 이 날 수 있다
    r = await _client().aio.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    if not r.text:
        raise ValueError("빈 응답")
    return schema.model_validate_json(r.text)


def _unavailable() -> BitokError:
    return BitokError(
        ErrorCode.LLM_UNAVAILABLE, "모델이 응답하지 않습니다.", status_code=502
    )


async def generate[T: BaseModel](
    system: str,
    contents: list,
    schema: type[T],
    check: Callable[[T], None] | None = None,
) -> T:
    """붐비거나 늦으면 예비 모델로 한 번 넘어간다. 사용자가 화면에서 기다리는 중이다"""
    for model in (settings.gemini_model, settings.gemini_fallback_model):
        try:
            result = await asyncio.wait_for(
                _call(model, system, contents, schema), settings.llm_timeout_sec
            )
            if check:
                check(result)
            return result
        except errors.ClientError as e:
            log.warning("%s 실패: %s", model, e)
            if e.code not in (404, 429):  # 모델이 없거나 붐빈다. 나머지는 키, 요청 문제
                raise _unavailable() from e
            error = _unavailable()
        except TimeoutError:
            log.warning("%s %s초 안에 응답 없음", model, settings.llm_timeout_sec)
            error = BitokError(
                ErrorCode.LLM_TIMEOUT, "모델 응답이 너무 늦습니다.", status_code=504
            )
        except (
            errors.APIError,
            httpx.TransportError,
            ValidationError,
            ValueError,
        ) as e:
            log.warning("%s 실패: %r", model, e)
            error = _unavailable()
    raise error
