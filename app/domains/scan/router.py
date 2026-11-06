from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from app.core.db import SessionDep
from app.core.deps import CurrentUser
from app.core.errors import UNAUTHORIZED, ErrorCode, responses
from app.domains.scan import analysis, service
from app.domains.scan.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnswerRequest,
    ResultResponse,
    ScanResponse,
)

router = APIRouter(prefix="/api/scans", tags=["스캔/분석"])


@router.post(
    "",
    response_model=ScanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="메뉴판 촬영 및 인식",
    description=(
        "사진을 올리면 메뉴명을 읽어 돌려준다. 가격은 추출하지 않는다.\n\n"
        "스파이크 측정값 기준, 응답까지 10~25초 걸릴 것으로 예상한다. "
        "메뉴가 많은 메뉴판일수록 느리다.\n\n"
        "앱은 업로드 전에 긴 변 1600px, JPEG 85 로 줄인다. "
        "원본을 그대로 올리면 더 오래 걸린다.\n\n"
        "### 실패했을 때\n\n"
        "| error.code | 화면 |\n"
        "| --- | --- |\n"
        "| `NO_MENU_DETECTED` | 글씨 추출 실패->재촬영 필요 |\n"
        "| `OCR_LOW_CONFIDENCE` | 흐린 사진->재촬영을 권장 |\n"
        "| `INVALID_IMAGE` | 형식/크기 문제 |\n"
        "| `PROFILE_REQUIRED` | 식이 프로필이 없음 |\n"
        "| `LLM_UNAVAILABLE` / `LLM_TIMEOUT` | LLM오류 |\n\n"
        "### 추가 고려사항\n\n"
        "- `items[].name_en` — 로마자 표기. 번역 아님 (불고기 -> Bulgogi). "
        "사전에 있는 메뉴면 사전 표기를 쓴다\n"
        "- `items[].confidence` — 0.7 미만이면 살짝 못 믿김...\n"
        "- `items[].temp_id` — 이 스캔 안에서만 쓰는 임시 id. 무시하세요.\n"
        "- `image_quality` — ok, blurry, cropped 셋 중 하나. "
    ),
    responses=responses(
        UNAUTHORIZED,
        (400, "이미지 형식 크기 문제", ErrorCode.INVALID_IMAGE),
        (404, "없는 식당"),
        (
            422,
            "식이 프로필 미등록 또는 메뉴를 하나도 읽지 못함",
            ErrorCode.NO_MENU_DETECTED,
        ),
        (502, "모델 응답 실패"),
        (504, "모델 타임아웃"),
    ),
)
async def create_scan(
    user_id: CurrentUser,
    session: SessionDep,
    image: Annotated[UploadFile, File(description="메뉴판 사진")],
    restaurant_id: Annotated[
        int | None,
        Form(
            examples=[7],
        ),
    ] = None,
) -> ScanResponse:
    return await service.create_scan(session, user_id, image, restaurant_id)


@router.post(
    "/{scan_id}/analyze",
    response_model=AnalyzeResponse,
    summary="메뉴 분석 및 질문 생성",
    description=(
        "`items` 가 화면에 남아 있는 전체 목록이고, "
        "`selected_temp_ids` 가 분석할 항목이다.\n\n"
        "함께 돌려주는 질문은 확인이 필요한 재료에 대해서만 만들어진다. "
        "거의 확실히 들어가는 재료는 묻지 않고 바로 판정한다.\n\n"
        "questions 는 0~5개다. 넘칠 때는 알레르기 관련 질문을 먼저 넣는다. "
        "빈 배열이면 질문 화면을 건너뛰고 바로 결과로 간다.\n\n"
        "`menus[].matches` 에는 걸린 재료만 온다."
    ),
    responses=responses(
        UNAUTHORIZED,
        (400, "목록에 없는 temp_id 를 고름", ErrorCode.VALIDATION_ERROR),
        (404, "없는 스캔"),
        (422, "식이 프로필 미등록", ErrorCode.PROFILE_REQUIRED),
        (502, "모델 응답 실패"),
        (504, "모델 타임아웃"),
    ),
)
async def analyze(
    scan_id: int,
    body: AnalyzeRequest,
    user_id: CurrentUser,
    session: SessionDep,
) -> AnalyzeResponse:
    return await analysis.analyze(session, user_id, scan_id, body)


@router.post(
    "/{scan_id}/answers",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="직원 응답 기록",
    description=(
        "질문 하나에 답할 때마다 호출한다.\n\n"
        "식당을 선택한 스캔이면 이 기록이 그대로 **다른 사용자에게도 보이는 데이터**가 된다. "
        "같은 식당 같은 메뉴에 대한 다른 사용자의 응답과 함께 집계된다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 질문")),
)
async def answer(
    scan_id: int,
    body: AnswerRequest,
    user_id: CurrentUser,
) -> None:
    raise NotImplementedError


@router.get(
    "/{scan_id}/result",
    response_model=ResultResponse,
    summary="최종 결과 조회",
    description=(
        "모든 질문이 끝난 뒤 호출한다. 직원 응답을 반영한 최종 판정을 돌려준다.\n\n"
        "확인하지 않은 차단 성분이 남아 있으면 `safe` 를 주지 않는다."
    ),
    responses=responses(UNAUTHORIZED, (404, "없는 스캔")),
)
async def result(scan_id: int, user_id: CurrentUser) -> ResultResponse:
    raise NotImplementedError
