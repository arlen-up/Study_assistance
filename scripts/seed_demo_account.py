"""시연용 학생 계정 만들기 (다시 실행해도 안전).

하는 일
  1. Supabase에 시연 계정(이메일 인증 완료 상태)을 만들거나, 이미 있으면 비밀번호를 새로 정한다
  2. 실제 앱 화면 기능으로 로그인 → 학교 계정 연결 → 강의 자료 가져오기 (챗봇 검색이 진짜로 동작하도록)
  3. 관심 직무·펫 이름을 정하고, 최근 약 3달 치 학습 기록(잔디)을 채운다
     - 어제까지 9일 연속 학습 (펫 🦋 단계), 오늘은 비워 둠 → 시연 중 퀴즈를 풀면 잔디·펫이 바뀌는 모습
     - '오늘 복습할 교안' 2개가 뜨도록 날짜를 맞춤

실행 (앱이 로컬에서 실행 중이어야 함: uvicorn api.index:app --port 8000)
  python scripts/seed_demo_account.py --base-url http://localhost:8000
(주의: 이미 같은 계정의 학습 기록이 있으면 지우고 새로 채운다)

로그인 정보는 화면에만 출력한다 (파일·Git에 저장하지 않음).
"""

import argparse
import datetime as dt
import os
import random
import re
import secrets
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

DEMO_EMAIL = "lms-demo@example.com"
DEMO_NAME = "시연학생"
TARGET_JOB = "security"
PET_NAME = "나비"
KST = dt.timezone(dt.timedelta(hours=9))
STREAK_DAYS = 9          # 어제까지 연속 학습일
HISTORY_DAYS = 95        # 잔디를 채울 기간


def admin(url: str, key: str) -> httpx.Client:
    return httpx.Client(base_url=url, headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)


def upsert_auth_user(sb: httpx.Client, password: str) -> str:
    page = 1
    while True:
        res = sb.get("/auth/v1/admin/users", params={"page": page, "per_page": 100})
        res.raise_for_status()
        users = res.json().get("users", [])
        found = next((u for u in users if (u.get("email") or "").lower() == DEMO_EMAIL), None)
        if found:
            sb.put(f"/auth/v1/admin/users/{found['id']}", json={"password": password, "email_confirm": True}).raise_for_status()
            return found["id"]
        if len(users) < 100:
            break
        page += 1
    res = sb.post(
        "/auth/v1/admin/users",
        json={"email": DEMO_EMAIL, "password": password, "email_confirm": True,
              "user_metadata": {"display_name": DEMO_NAME}},
    )
    res.raise_for_status()
    return res.json()["id"]


def use_app(base_url: str, password: str) -> None:
    """실제 앱 화면 기능으로 학교 계정 연결과 강의 자료 가져오기를 한다."""
    headers = {"Origin": base_url}  # 앱의 CSRF(출처) 검사 통과
    with httpx.Client(base_url=base_url, headers=headers, timeout=120, follow_redirects=False) as app:
        res = app.post("/login", data={"email": DEMO_EMAIL, "password": password})
        if res.status_code != 303:
            sys.exit(f"로그인 실패 (HTTP {res.status_code})")
        for path in ("/ui/lms/link", "/ui/lms/sync"):
            res = app.post(path, headers={"HX-Request": "true"})
            res.raise_for_status()
            print(f"  {path} → {res.status_code}")


def seed_logs(rest: httpx.Client, user_id: str, today: dt.date) -> int:
    docs = rest.get("/rest/v1/documents", params={"select": "id,title", "order": "created_at.asc"}).json()
    # 제목이 사실상 같은 교안(띄어쓰기·괄호만 다름)은 하나만 쓴다 → 복습 목록에 같은 교안이 두 번 나오지 않게
    unique: dict[str, str] = {}
    for d in docs:
        unique.setdefault(re.sub(r"[^0-9A-Za-z가-힣]", "", d["title"]), d["id"])
    ids = list(unique.values())
    if len(ids) < 4:
        sys.exit(f"서로 다른 교안이 {len(ids)}개뿐이에요. 4개 이상 필요해요.")
    rng = random.Random(20261105)
    due_1d, due_3d, upcoming, *history = ids
    history = history or [upcoming]

    def at(days_ago: int) -> dt.datetime:
        day = today - dt.timedelta(days=days_ago)
        return dt.datetime(day.year, day.month, day.day, rng.randint(19, 22), rng.randint(0, 59), tzinfo=KST)

    logs: list[tuple[int, str]] = []
    # 오래된 기록: 평일 위주, 가끔 쉬는 주
    for days_ago in range(HISTORY_DAYS, STREAK_DAYS + 1, -1):  # STREAK_DAYS+1일 전은 비워서 연속 기록을 끊음
        day = today - dt.timedelta(days=days_ago)
        chance = 0.55 if day.weekday() < 5 else 0.2
        if rng.random() < chance:
            for _ in range(rng.choice([1, 1, 2, 3])):
                logs.append((days_ago, rng.choice(history)))
    # 최근 연속 학습 (어제까지 STREAK_DAYS일)
    for days_ago in range(STREAK_DAYS, 0, -1):
        logs.append((days_ago, rng.choice(history)))
    # 망각 곡선: 1회 학습 후 하루 지남 → 오늘 복습 / 2회 학습 후 3일 지남 → 오늘 복습 / 3회, 2일 전 → 7일 뒤 예정
    logs += [(1, due_1d), (7, due_3d), (3, due_3d), (9, upcoming), (5, upcoming), (2, upcoming)]

    rows = []
    for days_ago, doc_id in logs:
        total = 3
        rows.append({
            "user_id": user_id, "document_id": doc_id, "score": rng.choice([2, 3, 3, 1]),
            "total_questions": total, "completed": True,
            "duration_seconds": rng.randint(90, 240), "studied_at": at(days_ago).isoformat(),
        })
    rest.delete("/rest/v1/study_logs", params={"user_id": f"eq.{user_id}"}).raise_for_status()
    rest.post("/rest/v1/study_logs", json=rows, headers={"Prefer": "return=minimal"}).raise_for_status()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
    password = "Demo-" + secrets.token_urlsafe(9)
    today = dt.datetime.now(KST).date()

    with admin(url, key) as sb:
        print("1) 시연 계정 만들기")
        user_id = upsert_auth_user(sb, password)
        # 학교 계정 연결 전에 이름을 정해야 가상 학교 시스템에도 같은 이름으로 들어간다
        sb.patch("/rest/v1/users", params={"id": f"eq.{user_id}"},
                 json={"display_name": DEMO_NAME, "target_job": TARGET_JOB, "pet_name": PET_NAME}).raise_for_status()
        print("2) 학교 계정 연결 + 강의 자료 가져오기")
        use_app(args.base_url.rstrip("/"), password)
        # 이미 연결돼 있던 경우에도 가상 학교 시스템의 이름을 맞춘다
        linked = sb.get("/rest/v1/users", params={"id": f"eq.{user_id}", "select": "lms_student_id"}).json()
        if linked and linked[0].get("lms_student_id"):
            sb.patch("/rest/v1/lms_students", params={"id": f"eq.{linked[0]['lms_student_id']}"},
                     json={"name": DEMO_NAME}).raise_for_status()
        print("3) 학습 기록 채우기")
        count = seed_logs(sb, user_id, today)
        print(f"  학습 기록 {count}개")

    print("\n✅ 완료! 시연 계정 로그인 정보 (다시 실행하면 비밀번호가 바뀌어요)")
    print(f"  이메일:   {DEMO_EMAIL}")
    print(f"  비밀번호: {password}")


if __name__ == "__main__":
    main()
