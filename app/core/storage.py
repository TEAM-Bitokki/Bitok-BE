"""업로드 파일 저장."""

import asyncio
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import settings


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


async def save_image(data: bytes, ext: str) -> str:
    """저장한 키를 돌려준다. scans.image_url 에 이 값이 들어간다"""
    key = f"scans/{datetime.now():%Y/%m}/{uuid4().hex}.{ext}"
    path = Path(settings.upload_dir) / key
    write = asyncio.ensure_future(asyncio.to_thread(_write, path, data))
    try:
        await asyncio.shield(write)
    except asyncio.CancelledError:
        write.add_done_callback(lambda _: path.unlink(missing_ok=True))
        raise
    return key


async def delete_image(key: str) -> None:
    path = Path(settings.upload_dir) / key
    await asyncio.to_thread(path.unlink, missing_ok=True)
