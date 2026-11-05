"""시연용 학생 계정 5개 만들기 (다시 실행해도 안전).

계정마다 학년·수강 과목·이수 학점·과제(제출 여부)·관심 직무·학습 기록이 달라서,
챗봇에 "졸업까지 몇 학점 남았어?", "남은 과제 알려줘"를 물으면 계정마다 다른 답이 나와요.

| 계정 | 학년 (입학) | 펫 단계 (연속 학습) | 이번 학기(2026-2) | 남은 과제 | 관심 직무 |
|---|---|---|---|---|---|
| lms-demo1@example.com | 1학년 (2026) | 🥚 알 — 기록 없음, 첫 퀴즈로 부화 | 자료구조·컴퓨터공학활용 등 | 4개 (24시간 안 1개) | 프론트엔드 |
| lms-demo2@example.com | 2학년 (2025) | 🐛 애벌레 — 2일 (오늘 하면 번데기) | 실제 e-class 시간표 6과목 | 4개 (24시간 안 3개) | AI·데이터 |
| lms-demo3@example.com | 2학년 (2025) | 🥚 알 — 끊김 (시무룩) | DB·네트워크·백엔드·자바 등 | 4개 (급한 것 없음) | 백엔드 |
| lms-demo4@example.com | 3학년 (2024) | 🌰 번데기 — 6일 (오늘 하면 나비) | 웹 해킹·네트워크 보안 등 | 5개 (24시간 안 1개) | 보안 |
| lms-demo5@example.com | 4학년 (2023) | 🦋 나비 — 21일 (최종 단계) | 캡스톤·재수강 (졸업 직전) | 3개 (마감 지난 것 1개) | AI·데이터 |

펫 단계는 연속 학습일로 정해져요 (🥚 0일 → 🐛 1일 → 🌰 3일 → 🦋 7일, backend/services/pet_service.py).
오늘 칸은 모두 비워 둬서, 시연 중 퀴즈 1개를 풀면 1·2·4번 계정은 바로 다음 단계로 진화해요.

하는 일 (앱 서버 없이 Supabase REST + 서버용 비밀 키만 사용)
  1. Supabase 로그인 계정 만들기(이메일 인증 완료) — 이미 있으면 비밀번호만 새로 정함
  2. 가상 학교 시스템(lms_*)에 학생을 고정 학번(2026900001~5)으로 만들고, 수강 이력을 통째로 교체
     + 과목별 과제를 만들거나 마감일을 '지금' 기준으로 다시 맞추고, 계정마다 제출한 과제를 다르게 넣음
  3. 도우미 users 행에 학교 계정 연결·이수 학점·관심 직무·펫 이름 저장
  4. 학습 기록(잔디·복습 일정)을 계정마다 다른 모양으로 새로 채움 (오늘은 비워 둠)

실행
  python scripts/seed_demo_accounts.py                 # 비밀번호 무작위 (5개 계정 공통)
  python scripts/seed_demo_accounts.py --password ...  # 비밀번호 직접 지정 (Supabase 규칙상 6자 이상)
  ※ 과제 마감일은 실행 시각 기준이라, 시연 직전에 한 번 더 실행하면 '24시간 안 마감'이 다시 맞춰져요.

주의: 이 5개 계정·학생의 학습 기록, 수강 이력, 과제 제출 기록은 지우고 새로 채운다.
      다른 사용자·학생 데이터는 건드리지 않는다. 비밀번호는 화면에만 출력한다 (저장 안 함).
"""

import argparse
import datetime as dt
import os
import random
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

KST = dt.timezone(dt.timedelta(hours=9))
DEPARTMENT = "소프트웨어융합과"
CURRENT_SEMESTER = "2026-2"

# 교안(documents)의 과목 이름이 학교 과목 이름과 다를 때 (예전 과목 이름으로 가져온 자료)
DOC_COURSE_ALIASES = {"데이터베이스": "CS202", "컴퓨터네트워크": "CS204"}

H = tuple[str, str, str, str | None]  # (과목 코드, 학기, 상태, 성적)


def done(semester: str, *pairs: str) -> list[H]:
    """'CS101:A+' 형식으로 이수 과목을 적는다. 성적이 F면 낙제(failed)."""
    rows: list[H] = []
    for pair in pairs:
        code, grade = pair.split(":")
        rows.append((code, semester, "failed" if grade in ("F", "NP") else "completed", grade))
    return rows


def taking(*codes: str) -> list[H]:
    return [(code, CURRENT_SEMESTER, "in_progress", None) for code in codes]


@dataclass(frozen=True)
class StudyPattern:
    run_len: int          # 최근 연속 학습일 수
    run_end: int          # 연속 학습이 끝난 날 (1 = 어제, 2 = 그제 → 지금 연속 기록 0일)
    history_days: int     # 잔디를 채울 기간
    weekday_chance: float
    weekend_chance: float
    seed: int
    fresh: bool = False   # True = 학습 기록이 하나도 없는 새 계정 (펫이 알에서 아직 안 깨어남)

    @property
    def expected_streak(self) -> int:
        return 0 if self.fresh or self.run_end > 1 else self.run_len


@dataclass(frozen=True)
class Assignment:
    course_code: str
    title: str
    due_in_hours: int            # 스크립트를 실행한 시각 기준 마감 (음수 = 이미 지남, 하루까지는 '지남'으로 보임)
    description: str | None = None  # None = 이미 있는 과제 (마감일만 새로 맞춤)


# 과제는 과목에 붙어 있어서, 같은 과목을 듣는 계정은 같은 과제를 받아요.
# 계정마다 다른 건 ① 듣는 과목 ② 이미 제출한 과제(Profile.submitted)예요.
ASSIGNMENTS: list[Assignment] = [
    # e-class 시연 데이터(마이그레이션)로 이미 들어 있는 과제 — 마감일만 다시 맞춤
    Assignment("CS202", "수강신청 시스템 ERD 설계", 20),
    Assignment("CS204", "TCP 3-way handshake 정리", 52),
    Assignment("CS204", "10주차.정리제출", 9),
    Assignment("CS311", "FastAPI CRUD API 구현", 76),
    Assignment("CS332", "XSS 취약점 분석 보고서", 100),
    Assignment("GE301", "[AI] 김영란법 : 부정청탁방지법", 56),
    Assignment("GE301", "[AI] 관인 청인 직인 특수관인 차이", 104),
    # 이 스크립트가 새로 만드는 과제
    Assignment("CS103", "명제와 진리표 연습문제", 30, "교재 2장 연습문제 1~10번을 풀고 진리표를 그려 제출하세요."),
    Assignment("CS104", "CPU 파이프라인 단계 정리", 98, "5단계 파이프라인(IF·ID·EX·MEM·WB)과 해저드 종류를 한 쪽으로 정리하세요."),
    Assignment("CS201", "연결 리스트 직접 구현하기", 16, "단일 연결 리스트의 삽입·삭제·탐색을 파이썬으로 구현하고 실행 결과를 첨부하세요."),
    Assignment("SW111", "엑셀 함수로 성적표 만들기", 70, "SUM·AVERAGE·IF·VLOOKUP으로 가상 성적표를 만들어 제출하세요."),
    Assignment("GE103", "나의 진로 로드맵 작성", 146, "졸업 후 3년까지의 목표와 학기별 준비 계획을 적어 제출하세요."),
    Assignment("GE104", "영어 자기소개 스피치 녹음", 44, "1분 분량 영어 자기소개를 녹음해 올리세요."),
    Assignment("CS203", "프로세스 스케줄링 비교 보고서", 118, "FCFS·SJF·RR을 같은 예제로 계산해 평균 대기 시간을 비교하세요."),
    Assignment("GE202", "창업 아이템 1쪽 기획서", 166, "문제·고객·해결책·수익 모델을 한 쪽으로 정리하세요."),
    Assignment("SW221", "pandas로 CSV 데이터 정리", 40, "결측치 처리와 그룹별 평균을 구하는 노트북 파일을 제출하세요."),
    Assignment("SW231", "상속과 인터페이스 실습", 22, "Shape 인터페이스와 Circle·Rectangle 클래스를 구현하세요."),
    Assignment("CS301", "요구사항 명세서(SRS) 작성", 74, "팀 프로젝트의 기능·비기능 요구사항을 SRS 양식으로 작성하세요."),
    Assignment("CS333", "Wireshark 패킷 캡처 분석", 21, "실습 환경에서 캡처한 HTTP·DNS 패킷을 분석해 보고서로 제출하세요."),
    Assignment("CS334", "디스크 이미지 타임라인 분석", 122, "제공된 실습용 디스크 이미지에서 파일 생성·삭제 타임라인을 만드세요."),
    Assignment("GE203", "AI 윤리 사례 토론문", 94, "생성형 AI 저작권 사례 하나를 골라 찬반 근거를 정리하세요."),
    Assignment("CS206", "fork·exec 실습 코드 제출", 14, "fork로 자식 프로세스를 만들고 exec로 ls를 실행하는 C 코드를 제출하세요."),
    Assignment("CS324", "프롬프트 엔지니어링 실험 보고서", 68, "같은 질문을 프롬프트 3가지로 바꿔 물어보고 결과를 비교하세요."),
    Assignment("CS390", "캡스톤 중간 발표 자료", 46, "진행 상황·시연 화면·남은 일정을 담은 발표 자료(10쪽 이내)를 올리세요."),
    Assignment("CS390", "9주차 주간 진행 보고서", -6, "이번 주에 한 일·막힌 점·다음 주 계획을 적어 주세요."),
]


@dataclass(frozen=True)
class Profile:
    no: int
    name: str
    admission_year: int
    target_job: str
    pet_name: str
    history: list[H]
    pattern: StudyPattern
    note: str = ""
    submitted: tuple[tuple[str, str], ...] = ()  # 이미 제출한 과제 (과목 코드, 과제 이름)
    student_no: str = field(init=False)
    email: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "student_no", f"202690000{self.no}")
        object.__setattr__(self, "email", f"lms-demo{self.no}@example.com")

    @property
    def grade_year(self) -> int:
        return 2026 - self.admission_year + 1


PROFILES: list[Profile] = [
    Profile(
        no=1, name="김새봄", admission_year=2026, target_job="frontend", pet_name="콩이",
        note="1학년 — 🥚 알: 아직 공부 기록이 없어요. 퀴즈 1개를 풀면 🐛 애벌레로 깨어나요",
        submitted=(("SW111", "엑셀 함수로 성적표 만들기"), ("GE103", "나의 진로 로드맵 작성")),
        history=[
            *done("2026-1", "GE101:A0", "GE102:B+", "GE105:B0", "CS101:A+", "CS102:B+", "CS211:A0"),
            *taking("CS103", "CS104", "CS201", "SW111", "GE103", "GE104"),
        ],
        pattern=StudyPattern(run_len=0, run_end=1, history_days=0, weekday_chance=0, weekend_chance=0, seed=1,
                             fresh=True),
    ),
    Profile(
        no=2, name="이하늘", admission_year=2025, target_job="ai_data", pet_name="하루",
        note="2학년 — 🐛 애벌레(2일 연속): 오늘 공부하면 🌰 번데기로 진화. 실제 e-class 시간표 6과목",
        submitted=(("CS204", "TCP 3-way handshake 정리"), ("SW111", "엑셀 함수로 성적표 만들기"),
                   ("GE301", "[AI] 김영란법 : 부정청탁방지법"), ("GE301", "[AI] 관인 청인 직인 특수관인 차이")),
        history=[
            *done("2025-1", "GE101:A0", "GE102:B+", "GE105:B0", "CS101:A+", "CS102:B0", "CS103:C+", "CS211:B+"),
            *done("2025-2", "GE103:P", "GE104:B0", "CS104:B+", "CS201:A0", "CS212:C+", "CS203:F"),
            *done("2026-1", "GE201:A0", "CS205:B+", "CS206:B0", "CS221:B+", "CS331:A0"),
            *taking("CS202", "CS204", "SW111", "SW221", "SW231", "GE301"),
        ],
        pattern=StudyPattern(run_len=2, run_end=1, history_days=95, weekday_chance=0.55, weekend_chance=0.2, seed=2),
    ),
    Profile(
        no=3, name="박도윤", admission_year=2025, target_job="backend", pet_name="모모",
        note="2학년 — 🥚 알(끊김): 이틀 쉬어 연속 기록이 끊겨 알로 돌아갔어요 (펫이 시무룩). 백엔드 준비",
        submitted=(("CS202", "수강신청 시스템 ERD 설계"), ("CS204", "10주차.정리제출"), ("SW231", "상속과 인터페이스 실습")),
        history=[
            *done("2025-1", "GE101:B0", "GE102:B0", "GE105:C+", "CS101:B+", "CS102:B0", "CS211:B0"),
            *done("2025-2", "GE103:P", "GE104:B+", "CS103:C0", "CS104:B0", "CS201:B+", "CS212:A0"),
            *done("2026-1", "GE201:B+", "GE204:A0", "CS205:B+", "CS206:B0", "CS221:A0", "CS341:A0"),
            *taking("CS202", "CS203", "CS204", "CS311", "SW231", "GE202"),
        ],
        pattern=StudyPattern(run_len=5, run_end=2, history_days=95, weekday_chance=0.3, weekend_chance=0.1, seed=3),
    ),
    Profile(
        no=4, name="최민서", admission_year=2024, target_job="security", pet_name="별이",
        note="3학년 — 🌰 번데기(6일 연속): 오늘 공부하면 🦋 나비로 진화. 보안 트랙",
        submitted=(("SW221", "pandas로 CSV 데이터 정리"),),
        history=[
            *done("2024-1", "GE101:A0", "GE102:A0", "GE105:B+", "CS101:A0", "CS102:A+", "CS211:B+"),
            *done("2024-2", "GE103:P", "GE104:A0", "CS103:B0", "CS104:B+", "CS201:A0", "CS212:B+"),
            *done("2025-1", "GE201:A0", "CS202:A0", "CS203:F", "CS204:A+", "CS205:B+", "CS331:A+"),
            *done("2025-2", "GE204:B+", "CS203:B+", "CS206:B0", "CS221:B+", "CS315:B0", "CS341:A0"),
            *done("2026-1", "GE202:B0", "CS311:B+", "CS321:B0", "CS322:B+", "CS335:A0"),
            *taking("CS301", "CS332", "CS333", "CS334", "SW221", "GE203"),
        ],
        pattern=StudyPattern(run_len=6, run_end=1, history_days=120, weekday_chance=0.6, weekend_chance=0.3, seed=4),
    ),
    Profile(
        no=5, name="정우진", admission_year=2023, target_job="ai_data", pet_name="뭉치",
        note="4학년 — 🦋 나비(21일 연속, 최종 단계). 졸업 직전, 캡스톤 + 시스템 프로그래밍 재수강만 남음",
        submitted=(("GE301", "[AI] 김영란법 : 부정청탁방지법"), ("GE301", "[AI] 관인 청인 직인 특수관인 차이"),
                   ("CS324", "프롬프트 엔지니어링 실험 보고서")),
        history=[
            *done("2023-1", "GE101:B+", "GE102:B0", "GE105:A0", "CS101:A+", "CS102:A0", "CS211:A0"),
            *done("2023-2", "GE103:P", "GE104:B+", "CS103:B+", "CS104:A0", "CS201:A+", "CS212:A0"),
            *done("2024-1", "GE201:A0", "CS202:A+", "CS203:A0", "CS204:B+", "CS205:A0", "CS221:A0"),
            *done("2024-2", "GE203:A0", "CS206:F", "CS301:B+", "CS321:A+", "CS322:A0", "CS341:B+"),
            *done("2025-1", "GE202:A0", "CS311:A0", "CS312:B+", "CS313:B+", "CS323:A0", "CS331:B0"),
            *done("2025-2", "GE204:B+", "CS314:B0", "CS315:B+", "CS342:A0", "CS391:P"),
            *done("2026-1", "GE205:B+", "CS332:B+", "CS333:B0", "CS334:A0"),
            *taking("CS206", "CS324", "CS390", "GE301"),
        ],
        pattern=StudyPattern(run_len=21, run_end=1, history_days=150, weekday_chance=0.7, weekend_chance=0.4, seed=5),
    ),
]


# ---------------------------------------------------------------------
# Supabase REST
# ---------------------------------------------------------------------
def client(url: str, key: str) -> httpx.Client:
    return httpx.Client(base_url=url, headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)


def check(res: httpx.Response, action: str) -> httpx.Response:
    if res.is_error:
        sys.exit(f"❌ {action} 실패 (HTTP {res.status_code}): {res.text[:300]}")
    return res


def find_auth_user(sb: httpx.Client, email: str) -> dict | None:
    page = 1
    while True:
        res = check(sb.get("/auth/v1/admin/users", params={"page": page, "per_page": 100}), "로그인 계정 목록 조회")
        users = res.json().get("users", [])
        found = next((u for u in users if (u.get("email") or "").lower() == email), None)
        if found or len(users) < 100:
            return found
        page += 1


def upsert_auth_user(sb: httpx.Client, p: Profile, password: str) -> str:
    meta = {"display_name": p.name, "name": p.name}
    found = find_auth_user(sb, p.email)
    if found:
        check(sb.put(f"/auth/v1/admin/users/{found['id']}",
                     json={"password": password, "email_confirm": True, "user_metadata": meta}), "비밀번호 변경")
        return found["id"]
    res = sb.post("/auth/v1/admin/users",
                  json={"email": p.email, "password": password, "email_confirm": True, "user_metadata": meta})
    return check(res, "로그인 계정 만들기").json()["id"]


def upsert_student(sb: httpx.Client, p: Profile) -> str:
    res = sb.post(
        "/rest/v1/lms_students",
        params={"on_conflict": "student_no"},
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        json={"student_no": p.student_no, "name": p.name, "department": DEPARTMENT, "admission_year": p.admission_year},
    )
    return check(res, "가상 학교 학생 만들기").json()[0]["id"]


def replace_history(sb: httpx.Client, student_id: str, p: Profile, credits: dict[str, int]) -> None:
    check(sb.delete("/rest/v1/lms_course_history", params={"student_id": f"eq.{student_id}"}), "수강 이력 지우기")
    rows = [
        {"student_id": student_id, "course_code": code, "semester": sem, "status": status, "grade": grade,
         "credits": credits[code]}
        for code, sem, status, grade in p.history
    ]
    check(sb.post("/rest/v1/lms_course_history", json=rows, headers={"Prefer": "return=minimal"}), "수강 이력 넣기")
    # 시연 중 '제출 완료'를 눌렀던 과제도 다시 미제출로 (이 학생 것만)
    check(sb.delete("/rest/v1/lms_assignment_submissions", params={"student_id": f"eq.{student_id}"}),
          "과제 제출 기록 지우기")


def sync_assignments(sb: httpx.Client, now: dt.datetime) -> dict[tuple[str, str], str]:
    """과제를 만들거나(새 과제) 마감일만 다시 맞추고(기존 과제), (과목 코드, 과제 이름) → id 를 돌려준다."""
    def due(a: Assignment) -> str:
        return (now + dt.timedelta(hours=a.due_in_hours)).isoformat()

    new = [{"course_code": a.course_code, "title": a.title, "description": a.description, "due_at": due(a)}
           for a in ASSIGNMENTS if a.description is not None]
    check(sb.post("/rest/v1/lms_assignments", params={"on_conflict": "course_code,title"}, json=new,
                  headers={"Prefer": "resolution=merge-duplicates,return=minimal"}), "과제 만들기")
    for a in ASSIGNMENTS:
        if a.description is None:  # 이미 있는 과제: 설명·관련 자료는 그대로 두고 마감일만
            res = check(sb.patch("/rest/v1/lms_assignments",
                                 params={"course_code": f"eq.{a.course_code}", "title": f"eq.{a.title}"},
                                 json={"due_at": due(a)}, headers={"Prefer": "return=representation"}),
                        "과제 마감일 맞추기")
            if not res.json():
                sys.exit(f"❌ 과제가 없어요: {a.course_code} {a.title} (e-class 시연 데이터 SQL을 먼저 실행해 주세요)")
    rows = check(sb.get("/rest/v1/lms_assignments", params={"select": "id,course_code,title"}), "과제 목록 조회").json()
    return {(r["course_code"], r["title"]): r["id"] for r in rows}


def submit_assignments(sb: httpx.Client, student_id: str, p: Profile, ids: dict[tuple[str, str], str],
                       now: dt.datetime) -> None:
    if not p.submitted:
        return
    rows = [{"student_id": student_id, "assignment_id": ids[key],
             "submitted_at": (now - dt.timedelta(hours=6 + 11 * i)).isoformat()}
            for i, key in enumerate(p.submitted)]
    check(sb.post("/rest/v1/lms_assignment_submissions", json=rows, headers={"Prefer": "return=minimal"}),
          "과제 제출 기록 넣기")


def open_assignments(sb: httpx.Client, student_id: str) -> list[dict]:
    return check(sb.get("/rest/v1/lms_open_assignments",
                        params={"student_id": f"eq.{student_id}", "select": "course_name,title,due_at,is_overdue",
                                "order": "due_at.asc"}), "미제출 과제 조회").json()


def credit_summary(sb: httpx.Client, student_id: str) -> dict[str, int]:
    res = check(sb.post("/rest/v1/rpc/lms_get_credit_summary", json={"p_student_id": student_id}), "학점 요약 조회")
    return {row["category"]: int(row["earned_credits"]) for row in res.json()}


# ---------------------------------------------------------------------
# 학습 기록 (잔디 · 복습 일정)
# ---------------------------------------------------------------------
def _norm(text: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "").lower()


def document_pools(sb: httpx.Client, course_names: dict[str, str]) -> dict[str, list[str]]:
    """과목 코드 → 그 과목 교안 id 목록 (제목이 사실상 같은 교안은 하나만)."""
    docs = check(sb.get("/rest/v1/documents", params={"select": "id,title,course_name", "order": "created_at.asc"}),
                 "교안 목록 조회").json()
    by_name = {_norm(name): code for code, name in course_names.items()}
    seen: set[str] = set()
    pools: dict[str, list[str]] = {}
    for d in docs:
        name = _norm(d.get("course_name"))
        if not name:
            continue  # 과목이 없는 교안(개인 업로드 등)은 쓰지 않는다
        title_key = _norm(d["title"])
        if title_key in seen:
            continue
        seen.add(title_key)
        code = by_name.get(name) or DOC_COURSE_ALIASES.get(name) or next(
            (c for n, c in by_name.items() if n.startswith(name) or name.startswith(n)), None
        )
        if code:
            pools.setdefault(code, []).append(d["id"])
    return pools


def seed_logs(sb: httpx.Client, user_id: str, p: Profile, pools: dict[str, list[str]], today: dt.date) -> int:
    check(sb.delete("/rest/v1/study_logs", params={"user_id": f"eq.{user_id}"}), "학습 기록 지우기")
    if p.pattern.fresh:  # 새 계정: 기록 없음 → 잔디 비어 있고 펫은 알
        check(sb.patch("/rest/v1/users", params={"id": f"eq.{user_id}"}, json={"last_studied_at": None}),
              "최근 학습 시각 지우기")
        return 0
    taking_codes = [c for c, sem, status, _ in p.history if status == "in_progress"]
    done_codes = [c for c, _, status, _ in p.history if status != "in_progress"]
    def docs_of(codes: list[str]) -> list[str]:
        out: list[str] = []
        for code in codes:
            out += [d for d in pools.get(code, []) if d not in out]
        return out

    relevant = docs_of([*taking_codes, *done_codes])            # 내가 듣는·들었던 과목의 교안
    others = [d for d in docs_of(list(pools)) if d not in relevant]
    ordered = relevant + others
    if len(ordered) < 4:
        sys.exit(f"❌ 쓸 수 있는 교안이 {len(ordered)}개뿐이에요. 4개 이상 필요해요.")

    # 복습 알림용 교안 3개는 이번 학기 과목 교안을 먼저 쓰고, 나머지로 잔디를 채운다
    due_1d, due_3d, upcoming = ordered[:3]
    history = relevant[3:] if len(relevant) >= 5 else (relevant[3:] + others)[:3]
    history = history[:8]
    pat = p.pattern
    rng = random.Random(20261106 + pat.seed)

    def at(days_ago: int, just_after_midnight: bool = False) -> str:
        day = today - dt.timedelta(days=days_ago)
        hour, minute = (0, rng.randint(5, 35)) if just_after_midnight else (rng.randint(19, 22), rng.randint(0, 59))
        return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST).isoformat()

    logs: list[tuple[int, str]] = []
    run_days = range(pat.run_end, pat.run_end + pat.run_len)
    gap_day = pat.run_end + pat.run_len  # 이 날은 비워서 연속 기록을 끊는다
    for days_ago in range(pat.history_days, gap_day, -1):
        day = today - dt.timedelta(days=days_ago)
        if rng.random() < (pat.weekday_chance if day.weekday() < 5 else pat.weekend_chance):
            logs += [(days_ago, rng.choice(history)) for _ in range(rng.choice([1, 1, 2, 3]))]
    logs += [(d, rng.choice(history)) for d in run_days]
    # 잔디용 교안은 4회 이상 공부해서 '복습 완료'가 되게 (이미 공부한 날에만 더해서 잔디 모양은 그대로)
    active_days = sorted({d for d, _ in logs if d > pat.run_end + 8} or {d for d, _ in logs})
    for doc_id in history:
        for _ in range(4 - sum(1 for _, x in logs if x == doc_id)):
            logs.append((rng.choice(active_days), doc_id))
    # 망각 곡선: 1회 학습 후 하루 지남 → 오늘 복습 / 2회 학습 후 3일 지남 → 오늘 복습 / 3회 → 7일 뒤 예정
    # (오늘 복습 2개는 자정 직후 기록으로 넣어서 오늘 하루 종일 '오늘 복습할 교안'에 보이게)
    # 복습 기록이 '연속을 끊는 날(gap_day)'에 들어가면 연속 기록이 길어지므로, 그날부터는 하루씩 뒤로 민다
    e = pat.run_end
    def keep_gap(days_ago: int) -> int:
        return days_ago + 1 if days_ago >= gap_day else days_ago
    due_logs = [(keep_gap(e), due_1d), (keep_gap(e + 6), due_3d), (keep_gap(e + 2), due_3d)]
    logs += [(keep_gap(e + 8), upcoming), (keep_gap(e + 4), upcoming), (keep_gap(e + 1), upcoming)]

    rows = [
        {"user_id": user_id, "document_id": doc_id, "score": rng.choice([1, 2, 3, 3]), "total_questions": 3,
         "completed": True, "duration_seconds": rng.randint(90, 240), "studied_at": at(days_ago, early)}
        for (days_ago, doc_id), early in [*((log, False) for log in logs), *((log, True) for log in due_logs)]
    ]
    check(sb.post("/rest/v1/study_logs", json=rows, headers={"Prefer": "return=minimal"}), "학습 기록 넣기")
    last = max(r["studied_at"] for r in rows)
    check(sb.patch("/rest/v1/users", params={"id": f"eq.{user_id}"}, json={"last_studied_at": last}), "최근 학습 시각 저장")
    return len(rows)


PET_STAGES = [(7, "🦋 나비"), (3, "🌰 번데기"), (1, "🐛 애벌레"), (0, "🥚 알")]  # pet_service.py 와 같은 기준


def actual_streak(sb: httpx.Client, user_id: str) -> tuple[int, int]:
    """앱과 같은 DB 함수로 (현재 연속 학습일, 공부한 날 수)를 계산한다. 오늘 안 했어도 어제까지 이어지면 유지."""
    days = check(sb.post("/rest/v1/rpc/get_study_heatmap", json={"p_user_id": user_id, "p_days": 365}),
                 "잔디 조회").json()
    done_flags = [bool(d["is_completed"]) for d in days]
    tail = done_flags if done_flags and done_flags[-1] else done_flags[:-1]
    current = 0
    for flag in reversed(tail):
        if not flag:
            break
        current += 1
    return current, sum(done_flags)


def pet_stage(streak: int, active_days: int) -> str:
    label = next(label for need, label in PET_STAGES if streak >= need)
    if active_days == 0:
        return f"{label} (새 계정)"
    return f"{label} (끊김)" if streak == 0 else label


# ---------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--password", help="5개 계정 공통 비밀번호 (비우면 무작위)")
    args = parser.parse_args()
    if args.password is not None and len(args.password) < 6:
        sys.exit("비밀번호는 6자 이상이어야 해요.")

    load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
    password = args.password or "Demo-" + secrets.token_urlsafe(9)
    today = dt.datetime.now(KST).date()

    with client(url, key) as sb:
        courses = check(sb.get("/rest/v1/lms_courses", params={"select": "code,name,credits"}), "과목 목록 조회").json()
        credits = {c["code"]: int(c["credits"]) for c in courses}
        names = {c["code"]: c["name"] for c in courses}
        grad = check(sb.get("/rest/v1/lms_graduation_requirements",
                            params={"department": f"eq.{DEPARTMENT}", "select": "total_credits"}), "졸업 요건 조회").json()
        if not grad:
            sys.exit(f"❌ '{DEPARTMENT}' 졸업 요건이 없어요.")
        total_required = int(grad[0]["total_credits"])
        jobs = {t["job_role"] for t in check(sb.get("/rest/v1/career_tracks", params={"select": "job_role"}),
                                              "직무 목록 조회").json()}
        listed = {(a.course_code, a.title) for a in ASSIGNMENTS}
        for p in PROFILES:
            unknown = sorted({code for code, *_ in p.history} - credits.keys())
            if unknown or p.target_job not in jobs:
                sys.exit(f"❌ {p.email}: 없는 과목 {unknown} 또는 없는 직무 {p.target_job}")
            taking_now = {c for c, _, status, _ in p.history if status == "in_progress"}
            bad = [key for key in p.submitted if key not in listed or key[0] not in taking_now]
            if bad:
                sys.exit(f"❌ {p.email}: 목록에 없거나 듣지 않는 과목의 과제 {bad}")
        pools = document_pools(sb, names)
        now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        assignment_ids = sync_assignments(sb, now)
        print(f"▶ 과제 {len(ASSIGNMENTS)}개 준비 (마감일은 지금 기준으로 다시 맞춤)")

        summary = []
        for p in PROFILES:
            print(f"▶ {p.email} ({p.name}, {p.grade_year}학년)")
            user_id = upsert_auth_user(sb, p, password)
            student_id = upsert_student(sb, p)
            others = check(sb.get("/rest/v1/users", params={"lms_student_id": f"eq.{student_id}",
                                                             "id": f"neq.{user_id}", "select": "id"}),
                           "학생 연결 확인").json()
            if others:
                sys.exit(f"❌ 학번 {p.student_no}이 이미 다른 사용자와 연결돼 있어요. 건드리지 않고 멈춰요.")
            replace_history(sb, student_id, p, credits)
            submit_assignments(sb, student_id, p, assignment_ids, now)
            earned = credit_summary(sb, student_id)
            completed = sum(earned.values())
            check(sb.patch("/rest/v1/users", params={"id": f"eq.{user_id}"}, json={
                "display_name": p.name, "target_job": p.target_job, "pet_name": p.pet_name,
                "lms_student_id": student_id, "completed_credits": completed,
                "lms_synced_at": dt.datetime.now(dt.UTC).isoformat(),
            }), "도우미 사용자 정보 저장")
            count = seed_logs(sb, user_id, p, pools, today)
            streak, active_days = actual_streak(sb, user_id)
            if streak != p.pattern.expected_streak:
                sys.exit(f"❌ {p.email}: 연속 학습일이 {streak}일이에요 (기대값 {p.pattern.expected_streak}일)")
            in_progress = [code for code, _, status, _ in p.history if status == "in_progress"]
            no_docs = [names[c] for c in in_progress if c not in pools]
            todo = open_assignments(sb, student_id)
            stage = pet_stage(streak, active_days)
            summary.append((p, completed, sum(credits[c] for c in in_progress), in_progress, count, no_docs, todo,
                            streak, stage))
            print(f"  펫 {stage} · 연속 {streak}일 · 이수 {completed}학점 · 수강 중 {len(in_progress)}과목"
                  f" · 남은 과제 {len(todo)}개 · 학습 기록 {count}개")

    hint = "직접 정한 값" if args.password else "무작위 — 다시 실행하면 바뀌어요"
    print(f"\n✅ 완료! 5개 계정 공통 비밀번호 ({hint}): {password}\n")
    for p, completed, now_credits, codes, count, no_docs, todo, streak, stage in summary:
        print(f"- {p.email}  {p.name} · {p.grade_year}학년({p.admission_year} 입학) · 관심 직무 {p.target_job}")
        print(f"    이수 {completed}학점 / 졸업 {total_required}학점 (남은 {max(total_required - completed, 0)}학점)"
              f" · 이번 학기 {now_credits}학점 · 펫 {stage} · 연속 학습 {streak}일 · 학습 기록 {count}개")
        print(f"    수강 중: {', '.join(names[c] for c in codes)}")
        for a in todo:
            due = dt.datetime.fromisoformat(a["due_at"]).astimezone(KST).strftime("%m/%d %H:%M")
            print(f"    {'⚠ 지남' if a['is_overdue'] else '□ 과제'} {a['course_name']} — {a['title']} (마감 {due})")
        if no_docs:
            print(f"    (교안 없음: {', '.join(no_docs)})")
        if p.note:
            print(f"    메모: {p.note}")


if __name__ == "__main__":
    main()
