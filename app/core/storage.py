"""업로드 파일 저장."""

import asyncio
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import settings


async def save_image(data: bytes, ext: str) -> str:
    """저장한 키를 돌려준다. scans.image_url 에 이 값이 들어간다"""
    key = f"scans/{datetime.now():%Y/%m}/{uuid4().hex}.{ext}"
    path = Path(settings.upload_dir) / key
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, data)
    return key
