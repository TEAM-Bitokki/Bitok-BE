"""공통 설정"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 앱
    app_name: str = "Bitok API"
    debug: bool = False

    # 데이터베이스
    database_url: str = "postgresql+asyncpg://bitok:bitok@localhost:5432/bitok"

    # 외부 서비스
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_model: str = "gemini-3.6-flash"  # 기본 모델이 붐빌 때
    kakao_rest_api_key: str = ""

    # AI 동작
    ocr_provider: str = "gemini"  # gemini | paddle
    mock_llm: bool = False  # true면 고정 응답
    llm_timeout_sec: int = 20
    max_staff_questions: int = 5  # 직원 질문 상한

    # 판정
    staff_check_window_days: int = 180  # 6개월 단위로 끊기

    # 이미지
    max_image_mb: int = 10
    upload_dir: str = "uploads"

    # /docs 보호
    docs_username: str = "bitok"
    docs_password: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
