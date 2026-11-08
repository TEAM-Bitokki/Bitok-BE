"""무한 스크롤 커서. (시각, id) 를 불투명한 문자열로"""

import base64
import binascii
import json
from datetime import datetime

from app.core.errors import BitokError, ErrorCode


def encode_cursor(at: datetime, row_id: int) -> str:
    """마지막 항목의 정렬 값을 다음 요청에서 사용할 문자열로 만든다."""
    payload = json.dumps(
        {"at": at.isoformat(), "id": row_id}, separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """커서를 (시각, id) 로 복원하고 잘못된 값은 422로 처리한다."""
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        at = datetime.fromisoformat(payload["at"])
        row_id = payload["id"]
        if (
            at.tzinfo is None
            or not isinstance(row_id, int)
            or isinstance(row_id, bool)
            or not 1 <= row_id <= 2**63 - 1  # BIGINT
        ):
            raise ValueError
    except (
        binascii.Error,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        raise BitokError(
            ErrorCode.VALIDATION_ERROR,
            "올바르지 않은 커서입니다.",
            status_code=422,
        ) from None
    return at, row_id
