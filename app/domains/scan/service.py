"""촬영 + 인식"""

import asyncio
import threading
from io import BytesIO
from statistics import median

from fastapi import UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import Dish, canonical_name
from app.core.config import settings
from app.core.errors import BitokError, ErrorCode
from app.core.storage import delete_image, save_image
from app.domains.profile.models import DietaryProfile
from app.domains.restaurant.models import Restaurant
from app.domains.scan.models import Scan
from app.domains.scan.ocr import extract_menu
from app.domains.scan.schemas import RecognizedItem, ScanResponse

LOW_CONFIDENCE = 0.5  # 절반 넘는 줄이 이보다 낮으면 사진 자체가 흐린 것
MAX_PIXELS = (
    20_000_000  # 앱은 긴 변 1600px 로 줄여 보낸다. 폰 원본(1200만)까지는 받는다
)
_decoding = threading.BoundedSemaphore(2)  # 서버 메모리 700MB 안에서 동시에 풀 장수
FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


def image_kind(data: bytes) -> tuple[str, str] | None:
    """앱이 보낸 Content-Type 대신 끝까지 열어본다. 깨진 파일은 여기서 걸린다"""
    try:
        with _decoding, Image.open(BytesIO(data), formats=list(FORMATS)) as img:
            if img.width * img.height > MAX_PIXELS:
                return None
            img.load()
            return FORMATS.get(img.format)
    except Exception:
        return None


def _one_line(text: str) -> str:
    """두 줄에 걸친 메뉴는 모델이 줄바꿈째 준다"""
    return " ".join(text.split())


async def create_scan(
    session: AsyncSession,
    user_id: int,
    image: UploadFile,
    restaurant_id: int | None,
) -> ScanResponse:
    if not await session.scalar(
        select(DietaryProfile.id).where(DietaryProfile.user_id == user_id)
    ):
        raise BitokError(
            ErrorCode.PROFILE_REQUIRED, "식이 프로필을 먼저 저장하세요.", 422
        )

    limit = settings.max_image_mb * 1024 * 1024
    data = await image.read(limit + 1)
    kind = await asyncio.to_thread(image_kind, data) if len(data) <= limit else None
    if kind is None:
        raise BitokError(
            ErrorCode.INVALID_IMAGE,
            f"{settings.max_image_mb}MB 이하의 JPEG PNG WEBP 만 올릴 수 있습니다.",
        )

    if restaurant_id is not None and not await session.get(Restaurant, restaurant_id):
        raise BitokError(ErrorCode.NOT_FOUND, "없는 식당입니다.", 404)

    await session.rollback()  # 모델을 기다리는 동안 DB 커넥션을 잡고 있지 않게
    result = await extract_menu(data, kind[1])
    items = [
        i.model_copy(
            update={"raw_name": _one_line(i.raw_name), "name_en": _one_line(i.name_en)}
        )
        for i in result.items
    ]
    items = [i for i in items if i.raw_name]
    if not items:
        raise BitokError(ErrorCode.NO_MENU_DETECTED, "메뉴를 찾지 못했습니다.", 422)
    if median(i.confidence for i in items) < LOW_CONFIDENCE:
        raise BitokError(
            ErrorCode.OCR_LOW_CONFIDENCE, "사진이 흐립니다. 다시 찍어주세요.", 422
        )

    keys = {canonical_name(i.raw_name) for i in items}
    known = dict(
        (
            await session.execute(
                select(Dish.canonical_name, Dish.name_en).where(
                    Dish.canonical_name.in_(keys)
                )
            )
        ).all()
    )
    recognized = [
        RecognizedItem(
            temp_id=f"i{n}",
            raw_name=i.raw_name,
            name_en=known.get(canonical_name(i.raw_name)) or i.name_en or None,
            confidence=round(i.confidence, 2),
        )
        for n, i in enumerate(items, 1)
    ]

    scan = Scan(
        user_id=user_id,
        restaurant_id=restaurant_id,
        image_url=(key := await save_image(data, kind[0])),
        raw_ocr={
            "items": [r.model_dump() for r in recognized],
            "image_quality": result.image_quality,
        },
    )
    session.add(scan)
    try:
        await session.commit()
    except Exception:
        await delete_image(key)
        raise

    return ScanResponse(
        scan_id=scan.id,
        restaurant_id=restaurant_id,
        items=recognized,
        image_quality=result.image_quality,
    )
