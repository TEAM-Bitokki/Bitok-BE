# Bitok

**Before you bite, check if it's OK.**

## 소개

Bitok은 Bite와 OK를 결합한 이름으로, 먹기 전 음식이 나의 식이 조건에 맞는지 확인한다는 의미입니다. 메뉴판을 촬영하면 알레르기·채식 조건에 맞춰 주의 정보를 안내하고, 직원에게 보여줄 한국어 확인 문장을 만들어줍니다.

FastAPI · PostgreSQL · SQLAlchemy + Alembic · GitHub Actions · AWS (EC2 + RDS)

## 실행

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements-dev.txt
copy .env.example .env

uvicorn app.main:app --reload
pytest
```

## 구조

```
app/
├── main.py              앱 생성, 라우터 등록, Swagger
├── core/
│   ├── config.py        환경변수
│   ├── errors.py        에러 코드, 공통 핸들러
│   └── deps.py          deviceToken 인증
├── common/
│   └── schemas.py       도메인 공통 타입
└── domains/             
    ├── auth/            가입·닉네임·탈퇴
    ├── profile/         식이 프로필
    ├── ingredient/      성분 검색
    ├── restaurant/      지도·식당·메뉴 상세
    ├── scan/            촬영·분석·직원 확인
    ├── check/           저장한 메뉴
    └── saved/           저장한 식당

tests/
└── test_contract.py     
```
