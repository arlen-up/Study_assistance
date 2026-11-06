"""LSSA 챗봇 AI 평가 — 질문 분류 · 출처 인용 · 근거 부족 판단 · 학적 사실 정확도 · 응답 시간.

평가 세트 evals/ai_eval_set.json 의 질문을 **실제 앱과 같은 코드(AgentService)**로 실제 AI·DB에 보내고,
사람이 미리 정한 기대값과 자동으로 비교한다. 결과는 두 곳에 남긴다.
  - docs/AI_평가.md              : 사람이 읽는 보고서 (표)
  - evals/results/<시각>.json      : 문항별 원자료 (질문·분류·근거·출처·도구·시간·답변 일부)

채점 기준 (문항 종류별)
  chat      : 일상으로 분류 · 근거 표시 없음 · 도구 안 씀
  study     : 학습으로 분류 · 기대한 교안을 [n]으로 인용 · 근거 '충분/일부'
  study_out : 학습으로 분류 · 근거 '부족'(지어내지 않음)
  info      : 학적으로 분류 · 학교 시스템 출처 표시 · 기대한 사실(숫자·과목)이 답변에 있음

실행: python scripts/eval_ai.py [--concurrency 3]
     python scripts/eval_ai.py --report-only evals/results/<시각>.json   (보고서만 다시 만들기)
(실제 AI를 부르므로 비용이 조금 든다 — 30문항 기준 AI 호출 약 60~90번)
"""

import argparse
import asyncio
import datetime as dt
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local")

from backend.core.config import get_settings  # noqa: E402
from backend.core.supabase_client import create_supabase  # noqa: E402
from backend.integrations.lms_client import LmsClient  # noqa: E402
from backend.schemas.models import ChatRequest, ChatResponse  # noqa: E402
from backend.services import lms_link_service  # noqa: E402
from backend.services.agent_service import AgentService  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
EVAL_SET = ROOT / "evals" / "ai_eval_set.json"
RESULTS_DIR = ROOT / "evals" / "results"
REPORT = ROOT / "docs" / "AI_평가.md"
IMPROVEMENTS = ROOT / "evals" / "개선_기록.md"  # 사람이 적는 개선 기록 (보고서 끝에 그대로 붙인다)
CATEGORY_LABEL = {"chat": "💬 일상", "study": "📚 학습(교안에 있음)", "study_out": "📚 학습(교안에 없음)", "info": "🎓 학적"}
EXPECTED_ROUTE = {"chat": "chat", "study": "study", "study_out": "study", "info": "info"}
EVIDENCE_LABEL = {"high": "충분", "medium": "일부", "low": "부족", None: "-"}


# ---------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------
def grade(item: dict[str, Any], res: ChatResponse) -> dict[str, bool]:
    cat = item["category"]
    route = res.route.type if res.route else None
    tools = [t.name for t in res.tool_calls]
    cited_titles = [c.title for c in res.citations if c.cited]
    checks: dict[str, bool] = {"분류": route == EXPECTED_ROUTE[cat]}
    if cat == "chat":
        checks["근거 표시 없음"] = res.evidence is None
        checks["도구 안 씀"] = not tools
    elif cat == "study":
        checks["맞는 교안 인용"] = any(item["expect_source"] in t for t in cited_titles)
        checks["근거 충분·일부"] = res.evidence in ("high", "medium")
    elif cat == "study_out":
        checks["근거 부족 표시"] = res.evidence == "low"
    elif cat == "info":
        checks["학교 시스템 출처"] = bool(res.lms_sources)
        answer = res.answer
        if "expect_all" in item:
            checks["사실 정확"] = all(x in answer for x in item["expect_all"])
        if "expect_any" in item:
            checks["사실 정확"] = any(x in answer for x in item["expect_any"])
        if "expect_tool" in item:
            checks["필요한 도구 사용"] = item["expect_tool"] in tools
    return checks


async def run_one(settings: Any, db: Any, lms: LmsClient, user_id: Any, student_id: Any,
                  item: dict[str, Any], sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        agent = AgentService(settings, db, lms=lms, lms_student_id=student_id, user_id=user_id)
        started = time.perf_counter()
        try:
            res = await agent.chat(ChatRequest(message=item["question"]))
        except Exception as exc:  # 평가에서는 실패도 결과로 남긴다
            ms = round((time.perf_counter() - started) * 1000)
            return {**item, "error": f"{type(exc).__name__}: {exc}"[:200], "ms": ms, "checks": {"응답": False},
                    "passed": False}
        ms = round((time.perf_counter() - started) * 1000)
        checks = grade(item, res)
        print(f"{'✅' if all(checks.values()) else '❌'} {item['id']:9} {ms/1000:5.1f}s  {item['question']}")
        return {
            **item,
            "route": res.route.type if res.route else None,
            "route_method": res.route.method if res.route else None,
            "evidence": res.evidence,
            "cited_titles": [c.title for c in res.citations if c.cited],
            "tools": [t.name for t in res.tool_calls],
            "lms_sources": [s.label for s in res.lms_sources],
            "answer": res.answer[:400],
            "ms": ms,
            "checks": checks,
            "passed": all(checks.values()),
        }


# ---------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------
def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({n / d * 100:.0f}%)" if d else "-"


def seconds(values: list[int]) -> str:
    if not values:
        return "-"
    p95 = sorted(values)[max(0, round(len(values) * 0.95) - 1)]
    return f"{statistics.median(values) / 1000:.1f}초 / {p95 / 1000:.1f}초"


def build_report(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    total = len(rows)
    by_cat = {c: [r for r in rows if r["category"] == c] for c in CATEGORY_LABEL}
    route_ok = sum(1 for r in rows if r.get("checks", {}).get("분류"))
    passed = sum(1 for r in rows if r["passed"])

    def check_rate(cat: str, name: str) -> str:
        items = [r for r in by_cat[cat] if name in r.get("checks", {})]
        return pct(sum(1 for r in items if r["checks"][name]), len(items))

    lines = [
        "# 🧪 AI 평가 결과",
        "",
        "> `python scripts/eval_ai.py`가 자동으로 만든 문서예요. 평가 세트: `evals/ai_eval_set.json`, "
        "문항별 원자료: `evals/results/`.",
        f"> 실행: {meta['started']} · 모델 `{meta['model']}` · 임베딩 `{meta['embedding']}` · "
        f"계정 `{meta['account']}` · {total}문항",
        "",
        "## 1. 한눈에 보기",
        "",
        "| 지표 | 결과 | 뜻 |",
        "|---|---|---|",
        f"| **전체 통과** | **{pct(passed, total)}** | 문항의 모든 채점 기준을 통과 |",
        f"| 질문 분류 정확도 | {pct(route_ok, total)} | 일상·학습·학적을 맞게 나눔 |",
        f"| 맞는 교안 인용 | {check_rate('study', '맞는 교안 인용')} | 교안에 있는 질문에 **기대한 교안**을 [n]으로 인용 |",
        f"| 근거 부족 판단 | {check_rate('study_out', '근거 부족 표시')} | 교안에 없는 질문을 지어내지 않고 '근거 부족' 표시 |",
        f"| 학적 사실 정확도 | {check_rate('info', '사실 정확')} | 이수 학점·졸업 요건·과목·과제가 학교 데이터와 일치 |",
        f"| 학교 시스템 출처 표시 | {check_rate('info', '학교 시스템 출처')} | 학적 답변에 어느 시스템의 어떤 데이터인지 표시 |",
        f"| 일상 대화에 근거 표시 안 함 | {check_rate('chat', '근거 표시 없음')} | 일상 대화에는 '근거 부족' 같은 표시를 붙이지 않음 |",
        f"| 응답 시간 (중앙값 / 95%) | {seconds([r['ms'] for r in rows])} | 질문 분류부터 답변 완성까지 |",
        "",
        "## 2. 질문 종류별",
        "",
        "| 종류 | 문항 | 통과 | 분류 정확도 | 응답 시간 (중앙값 / 95%) |",
        "|---|---|---|---|---|",
    ]
    for cat, label in CATEGORY_LABEL.items():
        items = by_cat[cat]
        lines.append(
            f"| {label} | {len(items)} | {pct(sum(r['passed'] for r in items), len(items))} | "
            f"{pct(sum(1 for r in items if r.get('checks', {}).get('분류')), len(items))} | "
            f"{seconds([r['ms'] for r in items])} |"
        )

    confusion = Counter((EXPECTED_ROUTE[r["category"]], r.get("route")) for r in rows)
    lines += ["", "### 질문 분류 혼동표 (행 = 정답, 열 = AI 판단)", "", "| 정답 \\ 판단 | chat | study | info |",
              "|---|---|---|---|"]
    for exp in ("chat", "study", "info"):
        lines.append(f"| **{exp}** | " + " | ".join(str(confusion.get((exp, got), 0)) for got in ("chat", "study", "info"))
                     + " |")
    methods = Counter(r.get("route_method") for r in rows)
    lines += ["", f"분류 방법: AI {methods.get('llm', 0)}건 · 규칙(AI 실패 시) {methods.get('rule', 0)}건", ""]

    lines += ["## 3. 문항별 결과", "", "| | 문항 | 질문 | 분류 | 근거 | 인용한 교안 / 사용한 도구 | 시간 | 실패한 기준 |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = "✅" if r["passed"] else "❌"
        detail = ", ".join(r.get("cited_titles") or []) or ", ".join(r.get("tools") or []) or "-"
        failed = ", ".join(k for k, v in r.get("checks", {}).items() if not v) or ""
        if r.get("error"):
            failed = f"오류: {r['error']}"
        lines.append(
            f"| {mark} | {r['id']} | {r['question']} | {r.get('route') or '-'} | "
            f"{EVIDENCE_LABEL.get(r.get('evidence'), '-')} | {detail[:80]} | {r['ms'] / 1000:.1f}s | {failed} |"
        )

    lines += [
        "",
        "## 4. 어떻게 채점하나요",
        "",
        "| 종류 | 통과 기준 (모두 만족해야 통과) |",
        "|---|---|",
        "| 💬 일상 | chat으로 분류 · 근거 표시 없음 · MCP 도구 안 씀 |",
        "| 📚 학습 (교안에 있음) | study로 분류 · **사람이 정해 둔 교안**을 답변에서 [n]으로 인용 · 근거 '충분' 또는 '일부' |",
        "| 📚 학습 (교안에 없음) | study로 분류 · 근거 '부족' (교안에 없는 내용을 교안 근거처럼 지어내지 않음) |",
        "| 🎓 학적 | info로 분류 · 학교 시스템 출처 표시 · 기대한 사실(숫자·과목·과제)이 답변에 있음 · 필요하면 해당 도구 사용 |",
        "",
        "- 정답(기대값)은 평가를 돌리기 전에 사람이 정해 `evals/ai_eval_set.json`에 적어 둬요. "
        "학적 정답은 시연 계정 데이터(`scripts/seed_demo_accounts.py`) 기준이에요.",
        "- AI 답변은 매번 조금씩 달라서, 같은 세트를 다시 돌리면 숫자가 조금 바뀔 수 있어요.",
        "- 한계: 문항 수가 적고(시연 계정 1명 기준), 답변 내용의 '설명 품질'은 사람이 따로 봐야 해요.",
        "",
    ]
    if IMPROVEMENTS.exists():
        lines += [IMPROVEMENTS.read_text(encoding="utf-8").strip(), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------
async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--report-only", metavar="JSON", help="AI를 다시 부르지 않고, 저장된 결과로 보고서만 다시 만든다")
    args = parser.parse_args()
    if args.report_only:
        saved = json.loads(Path(args.report_only).read_text(encoding="utf-8"))
        REPORT.write_text(build_report(saved["results"], saved["meta"]), encoding="utf-8")
        print(f"보고서를 다시 만들었어요: {REPORT.relative_to(ROOT)}")
        return

    data = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    settings = get_settings()
    db = await create_supabase(settings)
    users = await db.table("users").select("id").eq("email", data["account"]).limit(1).execute()
    if not users.data:
        sys.exit(f"❌ 평가 계정 {data['account']} 이 없어요. scripts/seed_demo_accounts.py 를 먼저 실행해 주세요.")
    user_id = users.data[0]["id"]
    from uuid import UUID

    started = dt.datetime.now(KST)
    async with LmsClient.connect(settings) as lms:
        student_id = await lms_link_service.get_student_id(db, UUID(user_id), lms)
        if student_id is None:
            sys.exit("❌ 평가 계정이 학교 계정에 연결돼 있지 않아요. 시연 계정 스크립트를 다시 실행해 주세요.")
        sem = asyncio.Semaphore(max(1, args.concurrency))
        rows = await asyncio.gather(*[
            run_one(settings, db, lms, UUID(user_id), student_id, item, sem) for item in data["items"]
        ])

    meta = {
        "started": started.strftime("%Y-%m-%d %H:%M KST"),
        "model": settings.llm_chat_model,
        "embedding": settings.llm_embedding_model,
        "account": data["account"],
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    raw = RESULTS_DIR / f"{started:%Y%m%d-%H%M}.json"
    raw.write_text(json.dumps({"meta": meta, "results": rows}, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    REPORT.write_text(build_report(list(rows), meta), encoding="utf-8")
    passed = sum(r["passed"] for r in rows)
    print(f"\n전체 통과 {passed}/{len(rows)} · 보고서 {REPORT.relative_to(ROOT)} · 원자료 {raw.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
