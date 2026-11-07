"""직원 응답, 최종 결과"""

import pytest
from sqlalchemy import func, select

from app.domains.restaurant.models import Restaurant
from app.domains.scan.models import Scan, StaffCheck
from tests.test_analyze import KIMCHI, H, analyze, info, llm_knows, profile


def answer(client, scan_id, question, value):
    return client.post(
        f"/api/scans/{scan_id}/answers",
        headers=H,
        json={"question_id": question["question_id"], "answer": value},
    )


def result(client, scan_id):
    return client.get(f"/api/scans/{scan_id}/result", headers=H)


@pytest.mark.parametrize(
    "value,verdict",
    [("no", "safe"), ("yes", "avoid"), ("unsure", "not_sure")],
)
@pytest.mark.asyncio
async def test_answer_decides_hidden_ingredient(
    client, session, scan, monkeypatch, value, verdict
):
    """새우젓이 안 들어간다고 하면 새우 알레르기에도 safe"""
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    [q] = analyze(client, scan, "김치찌개").json()["questions"]

    assert answer(client, scan, q, value).status_code == 204
    [m] = result(client, scan).json()["menus"]

    assert m["verdict"] == verdict
    assert [(x["ingredient"], x["verdict"]) for x in m["matches"]] == [
        ("새우", verdict)
    ]


@pytest.mark.asyncio
async def test_unanswered_stays_not_sure(client, session, scan, monkeypatch):
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    analyze(client, scan, "김치찌개")

    assert result(client, scan).json()["menus"][0]["verdict"] == "not_sure"


@pytest.mark.asyncio
async def test_question_cut_by_cap_stays_not_sure(client, session, scan, monkeypatch):
    """새우젓만 물었고 새우는 못 물었다. 새우젓이 없다고 해도 safe 가 아니다"""
    await profile(client, session, ["새우"])
    monkeypatch.setattr("app.core.config.settings.max_staff_questions", 1)
    llm_knows(
        monkeypatch,
        info(
            "해물볶음밥",
            "Haemulbokkeumbap",
            possible=[("새우젓", "Salted Shrimp"), ("새우", "Shrimp")],
        ),
    )
    questions = analyze(client, scan, "해물볶음밥").json()["questions"]
    for q in questions:
        answer(client, scan, q, "no")

    assert len(questions) == 1
    assert result(client, scan).json()["menus"][0]["verdict"] == "not_sure"


@pytest.mark.parametrize(
    "answers,verdict", [(["no"], "not_sure"), (["no", "no"], "safe")]
)
@pytest.mark.asyncio
async def test_broth_needs_every_question(
    client, session, scan, monkeypatch, answers, verdict
):
    """비건이면 육수에 고기도 해산물도 없다고 해야 safe"""
    await profile(client, session, diet="vegan")
    llm_knows(monkeypatch, info("냉면", "Naengmyeon", possible=[("육수", "Broth")]))
    questions = analyze(client, scan, "냉면").json()["questions"]
    for q, value in zip(questions, answers, strict=False):
        answer(client, scan, q, value)

    assert result(client, scan).json()["menus"][0]["verdict"] == verdict


@pytest.mark.asyncio
async def test_broth_source_cleared_by_answer(client, session, scan, monkeypatch):
    """육수에 밀이 든다고 모델이 말했어도, 직원이 아니라고 하면 safe"""
    await profile(client, session, ["밀"])
    llm_knows(
        monkeypatch, info("짬뽕", "Jjamppong", possible=[("육수", "Broth", ["밀"])])
    )
    [q] = analyze(client, scan, "짬뽕").json()["questions"]
    answer(client, scan, q, "no")

    assert result(client, scan).json()["menus"][0]["verdict"] == "safe"


@pytest.mark.asyncio
async def test_yes_inside_beats_no_outside(client, session, scan, monkeypatch):
    """해산물이 없다면서 해산물에 새우가 있다고 하면 avoid"""
    await profile(client, session, ["새우"], diet="vegan")
    llm_knows(
        monkeypatch, info("해물파전", "Haemulpajeon", possible=[("해산물", "Seafood")])
    )
    inside, whole = analyze(client, scan, "해물파전").json()["questions"]
    answer(client, scan, inside, "yes")
    answer(client, scan, whole, "no")

    assert result(client, scan).json()["menus"][0]["verdict"] == "avoid"


@pytest.mark.asyncio
async def test_same_question_asked_once(client, session, scan, monkeypatch):
    """계란 알레르기인 비건이어도 육수에 계란은 한 번만 묻는다"""
    await profile(client, session, ["계란"], diet="vegan")
    llm_knows(
        monkeypatch, info("냉면", "Naengmyeon", possible=[("육수", "Broth", ["계란"])])
    )
    questions = analyze(client, scan, "냉면").json()["questions"]
    texts = [q["question_ko"] for q in questions]

    assert len(texts) == len(set(texts))
    for q in questions:
        answer(client, scan, q, "no")
    assert result(client, scan).json()["menus"][0]["verdict"] == "safe"


@pytest.mark.asyncio
async def test_unknown_dish_needs_every_question(client, session, scan, monkeypatch):
    await profile(client, session, ["새우"], diet="pescatarian")
    llm_knows(monkeypatch, info("사장님특제", "Sajangnimteukje", unknown=True))
    questions = analyze(client, scan, "사장님특제").json()["questions"]

    answer(client, scan, questions[0], "no")
    assert result(client, scan).json()["menus"][0]["verdict"] == "not_sure"
    answer(client, scan, questions[1], "no")
    assert result(client, scan).json()["menus"][0]["verdict"] == "safe"


@pytest.mark.asyncio
async def test_new_allergy_after_answer(client, session, scan, monkeypatch):
    """답한 뒤 프로필에 대두를 추가하면 다시 판정한다. 두부가 있어 avoid"""
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    [q] = analyze(client, scan, "김치찌개").json()["questions"]
    answer(client, scan, q, "no")
    assert result(client, scan).json()["menus"][0]["verdict"] == "safe"

    await profile(client, session, ["새우", "대두"])
    assert result(client, scan).json()["menus"][0]["verdict"] == "avoid"


@pytest.mark.asyncio
async def test_answer_leaves_public_record_once(client, session, scan, monkeypatch):
    """식당을 고른 스캔이면 공개 기록을 남긴다. 다시 답하면 그 기록을 고친다"""
    place = Restaurant(kakao_place_id="1", name="명동교자", lat=37.56, lng=126.98)
    session.add(place)
    await session.flush()
    s = await session.get(Scan, scan)
    s.restaurant_id = place.id
    await session.commit()
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    [q] = analyze(client, scan, "김치찌개").json()["questions"]

    answer(client, scan, q, "unsure")
    answer(client, scan, q, "yes")

    rows = (await session.execute(select(StaffCheck.answer, StaffCheck.target))).all()
    assert rows == [("yes", "새우젓")]
    assert result(client, scan).json()["restaurant_name"] == "명동교자"


@pytest.mark.asyncio
async def test_no_public_record_without_restaurant(client, session, scan, monkeypatch):
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    [q] = analyze(client, scan, "김치찌개").json()["questions"]
    answer(client, scan, q, "no")

    assert await session.scalar(select(func.count()).select_from(StaffCheck)) == 0


@pytest.mark.asyncio
async def test_answers_and_result_are_private(client, session, scan, monkeypatch):
    await profile(client, session, ["새우"])
    llm_knows(monkeypatch, KIMCHI)
    [q] = analyze(client, scan, "김치찌개").json()["questions"]
    other = {"deviceToken": "7c9e6679-7425-40de-944b-e07fc1f90ae7"}
    client.post("/api/auth/sign-up", json={"device_token": other["deviceToken"]})

    r = client.post(
        f"/api/scans/{scan}/answers",
        headers=other,
        json={"question_id": q["question_id"], "answer": "no"},
    )
    assert r.status_code == 404
    assert client.get(f"/api/scans/{scan}/result", headers=other).status_code == 404
    assert answer(client, scan + 1, q, "no").status_code == 404


@pytest.mark.asyncio
async def test_result_before_analysis_404(client, session, scan):
    await profile(client, session)
    assert result(client, scan).status_code == 404


@pytest.mark.nodb
def test_scan_id_out_of_bigint_422(client):
    assert client.get(f"/api/scans/{2**63}/result", headers=H).status_code == 422
