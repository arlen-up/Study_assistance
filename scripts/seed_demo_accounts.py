"""시연용 학생 계정 5개 만들기 (다시 실행해도 안전).

계정마다 학년·수강 과목·이수 학점·관심 직무·학습 기록이 달라서,
챗봇에 "졸업까지 몇 학점 남았어?", "이번 학기 시간표 알려줘"를 물으면 계정마다 다른 답이 나와요.

| 계정 | 학년 (입학) | 이번 학기(2026-2) | 관심 직무 |
|---|---|---|---|
| lms-demo1@example.com | 1학년 (2026) | 자료구조·컴퓨터공학활용 등 | 프론트엔드 |
| lms-demo2@example.com | 2학년 (2025) | 실제 e-class 시간표 6과목 | AI·데이터 |
| lms-demo3@example.com | 2학년 (2025) | DB·네트워크·백엔드·자바 등 | 백엔드 |
| lms-demo4@example.com | 3학년 (2024) | 웹 해킹·네트워크 보안 등 | 보안 |
| lms-demo5@example.com | 4학년 (2023) | 캡스톤·재수강 (졸업 직전) | AI·데이터 |

하는 일 (앱 서버 없이 Supabase REST + 서버용 비밀 키만 사용)
  1. Supabase 로그인 계정 만들기(이메일 인증 완료) — 이미 있으면 비밀번호만 새로 정함
  2. 가상 학교 시스템(lms_*)에 학생을 고정 학번(2026900001~5)으로 만들고, 수강 이력을 통째로 교체
  3. 도우미 users 행에 학교 계정 연결·이수 학점·관심 직무·펫 이름 저장
  4. 학습 기록(잔디·복습 일정)을 계정마다 다른 모양으로 새로 채움 (오늘은 비워 둠)

실행
  python scripts/seed_demo_accounts.py                 # 비밀번호 무작위 (5개 계정 공통)
  python scripts/seed_demo_accounts.py --password ...  # 비밀번호 직접 지정

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
        note="1학년 — 첫 학기를 마치고 전공 기초를 듣는 중",
        history=[
            *done("2026-1", "GE101:A0", "GE102:B+", "GE105:B0", "CS101:A+", "CS102:B+", "CS211:A0"),
            *taking("CS103", "CS104", "CS201", "SW111", "GE103", "GE104"),
        ],
        pattern=StudyPattern(run_len=3, run_end=1, history_days=60, weekday_chance=0.35, weekend_chance=0.1, seed=1),
    ),
    Profile(
        no=2, name="이하늘", admission_year=2025, target_job="ai_data", pet_name="하루",
        note="2학년 — 실제 e-class 2026-2 시간표 6과목, 운영체제 재수강 필요",
        history=[
            *done("2025-1", "GE101:A0", "GE102:B+", "GE105:B0", "CS101:A+", "CS102:B0", "CS103:C+", "CS211:B+"),
            *done("2025-2", "GE103:P", "GE104:B0", "CS104:B+", "CS201:A0", "CS212:C+", "CS203:F"),
            *done("2026-1", "GE201:A0", "CS205:B+", "CS206:B0", "CS221:B+", "CS331:A0"),
            *taking("CS202", "CS204", "SW111", "SW221", "SW231", "GE301"),
        ],
        pattern=StudyPattern(run_len=12, run_end=1, history_days=95, weekday_chance=0.55, weekend_chance=0.2, seed=2),
    ),
    Profile(
        no=3, name="박도윤", admission_year=2025, target_job="backend", pet_name="모모",
        note="2학년 — 백엔드 준비, 최근 이틀 공부를 쉬어 연속 기록이 끊김",
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
        note="3학년 — 보안 트랙, 운영체제는 재수강으로 이수",
        history=[
            *done("2024-1", "GE101:A0", "GE102:A0", "GE105:B+", "CS101:A0", "CS102:A+", "CS211:B+"),
            *done("2024-2", "GE103:P", "GE104:A0", "CS103:B0", "CS104:B+", "CS201:A0", "CS212:B+"),
            *done("2025-1", "GE201:A0", "CS202:A0", "CS203:F", "CS204:A+", "CS205:B+", "CS331:A+"),
            *done("2025-2", "GE204:B+", "CS203:B+", "CS206:B0", "CS221:B+", "CS315:B0", "CS341:A0"),
            *done("2026-1", "GE202:B0", "CS311:B+", "CS321:B0", "CS322:B+", "CS335:A0"),
            *taking("CS301", "CS332", "CS333", "CS334", "SW221", "GE203"),
        ],
        pattern=StudyPattern(run_len=5, run_end=1, history_days=120, weekday_chance=0.6, weekend_chance=0.3, seed=4),
    ),
    Profile(
        no=5, name="정우진", admission_year=2023, target_job="ai_data", pet_name="뭉치",
        note="4학년 — 졸업 직전, 캡스톤 + 시스템 프로그래밍 재수강만 남음",
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
    e = pat.run_end
    due_logs = [(e, due_1d), (e + 6, due_3d), (e + 2, due_3d)]
    logs += [(e + 8, upcoming), (e + 4, upcoming), (e + 1, upcoming)]

    rows = [
        {"user_id": user_id, "document_id": doc_id, "score": rng.choice([1, 2, 3, 3]), "total_questions": 3,
         "completed": True, "duration_seconds": rng.randint(90, 240), "studied_at": at(days_ago, early)}
        for (days_ago, doc_id), early in [*((log, False) for log in logs), *((log, True) for log in due_logs)]
    ]
    check(sb.delete("/rest/v1/study_logs", params={"user_id": f"eq.{user_id}"}), "학습 기록 지우기")
    check(sb.post("/rest/v1/study_logs", json=rows, headers={"Prefer": "return=minimal"}), "학습 기록 넣기")
    last = max(r["studied_at"] for r in rows)
    check(sb.patch("/rest/v1/users", params={"id": f"eq.{user_id}"}, json={"last_studied_at": last}), "최근 학습 시각 저장")
    return len(rows)


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
        for p in PROFILES:
            unknown = sorted({code for code, *_ in p.history} - credits.keys())
            if unknown or p.target_job not in jobs:
                sys.exit(f"❌ {p.email}: 없는 과목 {unknown} 또는 없는 직무 {p.target_job}")
        pools = document_pools(sb, names)

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
            earned = credit_summary(sb, student_id)
            completed = sum(earned.values())
            check(sb.patch("/rest/v1/users", params={"id": f"eq.{user_id}"}, json={
                "display_name": p.name, "target_job": p.target_job, "pet_name": p.pet_name,
                "lms_student_id": student_id, "completed_credits": completed,
                "lms_synced_at": dt.datetime.now(dt.UTC).isoformat(),
            }), "도우미 사용자 정보 저장")
            count = seed_logs(sb, user_id, p, pools, today)
            in_progress = [code for code, _, status, _ in p.history if status == "in_progress"]
            no_docs = [names[c] for c in in_progress if c not in pools]
            summary.append((p, completed, sum(credits[c] for c in in_progress), in_progress, count, no_docs))
            print(f"  이수 {completed}학점 · 수강 중 {len(in_progress)}과목 · 학습 기록 {count}개")

    print(f"\n✅ 완료! 5개 계정 공통 비밀번호 (다시 실행하면 바뀌어요): {password}\n")
    for p, completed, now, codes, count, no_docs in summary:
        streak = p.pattern.run_len if p.pattern.run_end == 1 else 0
        print(f"- {p.email}  {p.name} · {p.grade_year}학년({p.admission_year} 입학) · 관심 직무 {p.target_job}")
        print(f"    이수 {completed}학점 / 졸업 {total_required}학점 (남은 {max(total_required - completed, 0)}학점)"
              f" · 이번 학기 {now}학점 · 연속 학습 {streak}일 · 학습 기록 {count}개")
        print(f"    수강 중: {', '.join(names[c] for c in codes)}")
        if no_docs:
            print(f"    (교안 없음: {', '.join(no_docs)})")
        if p.note:
            print(f"    메모: {p.note}")


if __name__ == "__main__":
    main()
