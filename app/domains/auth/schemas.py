from pydantic import BaseModel, Field, field_validator

from app.common.schemas import DietType
from app.core.deps import canonical_uuid4


def _normalize_nickname(value: object) -> object:
    if not isinstance(value, str):
        return value
    nickname = value.strip()
    if not nickname:
        raise ValueError("닉네임은 한 글자 이상이어야 합니다")
    return nickname


class SignUpRequest(BaseModel):
    device_token: str = Field(examples=["550e8400-e29b-41d4-a716-446655440000"])
    nickname: str | None = Field(default=None, max_length=20, examples=["Hyunjin"])

    @field_validator("device_token")
    @classmethod
    def _check_uuid4(cls, v: str) -> str:
        token = canonical_uuid4(v)
        if token is None:
            raise ValueError("UUID v4 형식이어야 합니다")
        return token

    @field_validator("nickname", mode="before")
    @classmethod
    def _check_nickname(cls, v: object) -> object:
        return _normalize_nickname(v)


class MeResponse(BaseModel):
    """앱 시작 시 호출: 가입 여부, 온보딩 완료 여부"""

    user_id: int = Field(examples=[1])
    nickname: str | None = Field(examples=["Hyunjin"])
    has_profile: bool = Field(examples=[True])
    diet_type: DietType | None = Field(default=None, examples=["vegetarian"])
    allergen_count: int = Field(default=0, examples=[4])


class NicknameRequest(BaseModel):
    nickname: str = Field(max_length=20, examples=["Hyunjin"])

    @field_validator("nickname", mode="before")
    @classmethod
    def _check_nickname(cls, v: object) -> object:
        return _normalize_nickname(v)


class DeleteAccountRequest(BaseModel):
    agree: bool = Field(examples=[True])
    delete_saved_menus: bool = Field(default=False, examples=[False])
