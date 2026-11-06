"""촬영 + 인식"""

from statistics import median

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import Dish, canonical_name
from app.core.config import settings
from app.core.errors import BitokError, ErrorCode
from app.core.storage import save_image
from app.domains.profile.models import DietaryProfile
from app.domains.restaurant.models import Restaurant
from app.domains.scan.models import Scan
from app.domains.scan.ocr import extract_menu
from app.domains.scan.schemas import RecognizedItem, ScanResponse

LOW_CONFIDENCE = 0.5  # 절반 넘는 줄이 이보다 낮으면 사진 자체가 흐린 것


def image_kind(data: bytes) -> tuple[str, str] | None:
    """앱이 보낸 Content-Type 대신 파일 앞부분을 본다"""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


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
    kind = image_kind(data)
    if kind is None or len(data) > limit:
        raise BitokError(
            ErrorCode.INVALID_IMAGE,
            f"{settings.max_image_mb}MB 이하의 JPEG PNG WEBP 만 올릴 수 있습니다.",
        )

    if restaurant_id is not None and not await session.get(Restaurant, restaurant_id):
        raise BitokError(ErrorCode.NOT_FOUND, "없는 식당입니다.", 404)

    result = await extract_menu(data, kind[1])
    items = [i for i in result.items if i.raw_name.strip()]
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
            raw_name=i.raw_name.strip(),
            name_en=known.get(canonical_name(i.raw_name)) or i.name_en,
            confidence=round(i.confidence, 2),
        )
        for n, i in enumerate(items, 1)
    ]

    scan = Scan(
        user_id=user_id,
        restaurant_id=restaurant_id,
        image_url=await save_image(data, kind[0]),
        raw_ocr={
            "items": [r.model_dump() for r in recognized],
            "image_quality": result.image_quality,
        },
    )
    session.add(scan)
    await session.commit()

    return ScanResponse(
        scan_id=scan.id,
        restaurant_id=restaurant_id,
        items=recognized,
        image_quality=result.image_quality,
    )
