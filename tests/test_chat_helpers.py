"""챗봇 마무리 처리(질문 분류 규칙·인용 번호·ID 지우기·근거 수준)와 마크다운 안전성 테스트.

AI·DB 없이 돌아가는 순수 함수만 확인한다:  python -m pytest -q tests
"""

import datetime as dt

from backend.core import semester
from backend.services.agent_service import (
    clean_answer,
    detect_course,
    info_prefetch,
    renumber_citations,
    rule_route,
    split_evidence,
)
from backend.web.markdown import render_markdown

START = dt.date(2026, 8, 31)   # 2026-2학기 1주차 월요일
TODAY = dt.date(2026, 11, 6)   # 10주차 금요일


# ---------------- 질문 분류 (AI 실패 시 규칙) ----------------
def test_rule_route_types() -> None:
    assert rule_route("졸업까지 몇 학점 남았어?")[0] == "info"
    assert rule_route("이번 주 마감 과제 알려줘")[0] == "info"
    assert rule_route("저번주 자바 내용 알려줘")[0] == "study"
    assert rule_route("DB 정규화")[0] == "study"
    assert rule_route("안녕 반가워")[0] == "chat"


def test_detect_course_aliases() -> None:
    assert detect_course("저번주 자바 내용 알려줘") == "자바"
    assert detect_course("자바스크립트 클로저가 뭐야") is None   # JavaScript는 자바가 아님
    assert detect_course("DB 정규화") == "db"
    assert detect_course("feedback 줘") is None                # 영어 단어 속 db는 무시


def test_info_prefetch_picks_tools() -> None:
    assert info_prefetch("졸업까지 몇 학점 남았어?") == ["career_analyze"]
    assert info_prefetch("이번 주 마감 과제") == ["lms_list_assignments"]
    assert "lms_get_transcript" in info_prefetch("내 성적 알려줘")


# ---------------- 학기 주차 ----------------
def test_resolve_week() -> None:
    assert semester.resolve_week("저번주 자바", START, TODAY) == 9
    assert semester.resolve_week("이번 주 수업", START, TODAY) == 10
    assert semester.resolve_week("다음 주 시험", START, TODAY) == 11
    assert semester.resolve_week("5주차 내용", START, TODAY) == 5
    assert semester.resolve_week("안전 주의", START, TODAY) is None


# ---------------- 답변 마무리 ----------------
def test_clean_answer_removes_ids_but_not_code() -> None:
    text = "예외 정리예요 (document_id: 3f2a1b4c-1111-2222-3333-444455556666).\n```sql\nselect '3f2a1b4c-1111-2222-3333-444455556666';\n```"
    cleaned = clean_answer(text)
    assert "document_id" not in cleaned.split("```")[0]
    assert "3f2a1b4c-1111-2222-3333-444455556666" in cleaned.split("```")[1]   # 코드 안은 그대로


def test_split_evidence() -> None:
    assert split_evidence("답변이에요\n근거수준: **high**") == ("답변이에요", "high")
    assert split_evidence("답변\n근거 수준: 부족") == ("답변", "low")
    assert split_evidence("근거수준 줄이 없는 답변") == ("근거수준 줄이 없는 답변", None)


def test_renumber_citations() -> None:
    text, mapping = renumber_citations("A [3]. B[1, 3]. 없는 번호 [9].\n```\narr[1]\n```", {1, 3})
    assert mapping == {3: 1, 1: 2}
    assert "A [1]." in text and "B[2][1]." in text
    assert "[9]" not in text
    assert "arr[1]" in text   # 코드 블록 안은 건드리지 않음


# ---------------- 마크다운 안전성 ----------------
def test_markdown_blocks_html_and_bad_links() -> None:
    html = str(render_markdown("<script>alert(1)</script> [x](javascript:alert(1)) ![i](http://evil/x.png)"))
    assert "<script>" not in html
    assert 'href="javascript' not in html
    assert "<img" not in html


def test_markdown_citations_mermaid_and_korean_bold() -> None:
    html = str(render_markdown("**예외(Exception)**는 오류예요 [1].\n```mermaid\nflowchart TD\n A[\"<b>x</b>\"] --> B\n```", [1], "cite-t"))
    assert "<strong>예외(Exception)</strong>는" in html
    assert 'href="#cite-t-1"' in html
    assert '<pre class="mermaid" data-diagram>' in html and "&lt;b&gt;" in html
