# LSSA (Study Overflow) — LMS 학습 도우미 & 커리어 나비게이터

> 미루는 습관, 파편화된 학사 정보, 그리고 단기 기억에 머무는 학습 해결

RAG + MCP 기반 학습·진로 도우미 (해커톤 「내 전공 현장을 바꾸는 AI」 출품작)
**파이썬 하나로** 화면(서버 렌더링), API, AI 에이전트, 가상 학교 시스템을 모두 만듭니다.

- **챗봇 (메인 화면)**: 질문 종류를 먼저 나누는 Router(💬 일상 / 📚 학습 / 🎓 학적) → 종류에 맞는 MCP 도구로 답변
  - 학습 질문: 교안을 찾아 `[1]` 번호로 인용하고, 밑에 **출처(쪽·소제목 위치)**와 근거 충분/일부/부족 표시 ("저번주 자바 내용 알려줘"도 학기 주차로 계산)
  - 학적 질문: 성적표·졸업 요건·과제를 학교 시스템에서 확인 / 일상 대화: 근거 표시 없이 바로 대답
  - 마크다운 답변, ERD·흐름도·마인드맵은 **그림(mermaid)**으로 표시, 사용한 MCP 도구는 눌러서 **입력/출력** 확인
  - 답변 밑 단추: ⏱ 3분 요약 · 🧠 퀴즈·카드 · 🗺 그림으로 정리 · 📄 교안 보기
- **교안 뷰어**: 챗봇 옆 빈 공간에 출처 교안을 띄움 (PDF는 해당 쪽으로, 글 교안은 답변에 쓴 부분을 노란색으로)
- **퀴즈·카드**: 객관식·O/X·빈칸 3문제를 카드로 풀고, 채점 뒤 카드를 뒤집어 정답·해설 확인 (예전 퀴즈 + 플래시카드를 하나로)
- **📊 대시보드**: 연속 학습·이번 주 학습·정답률·오늘 복습 + 주간 학습/정답률/과목별/복습 현황 그래프
- **복습 알림 · 학습 잔디**: 에빙하우스 망각 곡선(1·3·7일), GitHub식 1년 히트맵
- **가상 코스모스 연동**: Moodle 4.5 Web Services 규격의 모의 LMS (과제·마감·강의 자료)
- **알림 · 가상 펫**: 매일 저녁 하루 한 번 마감·복습 알림 (맥·윈도우 안내 + 기기에서 바로 확인, 메일 미리보기·카카오톡은 추가 개발 예정), 연속 학습에 따라 자라는 펫
- **시연 계정 5개**: 학년·수강 과목·이수 학점·관심 직무가 서로 다른 학생 (`scripts/seed_demo_accounts.py`)
- **커리어 나비게이터**: 졸업 요건 분석 + 관심 직무별 다음 학기 과목 추천 ("계산은 파이썬, 조언은 AI")

자세한 진행 기록은 [`docs/진행상황.md`](docs/진행상황.md)를 보세요.

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| 화면 | FastAPI + Jinja2 템플릿 + HTMX + 순수 CSS |
| API | FastAPI, Pydantic |
| AI | LangChain + MCP Python SDK, OpenAI GPT (`gpt-6-luna`, `text-embedding-3-small`) |
| DB·로그인 | Supabase (PostgreSQL, pgvector, Auth) |
| LMS 연동 | Moodle Web Services REST (코스모스 = Moodle 기반) |
| 배포 | GitHub + Vercel (Python 서버리스 함수, Cron) |

## 폴더 구조

```
api/index.py                Vercel 진입점
backend/                    📒 LMS 도우미
  app.py                    앱 조립: 라우터·오류 처리·보안 미들웨어, /lms 에 가상 학교 마운트
  mcp_server.py             MCP 서버 (@mcp.tool 도구 9개)
  integrations/             학교 시스템 연동: 게이트웨이, 코스모스 어댑터, Moodle 클라이언트
  services/                 에이전트(질문 분류·답변), 커리어, LMS 연결, 퀴즈, 복습, 잔디, 대시보드, 교안 뷰어·PDF 보관, 알림
  routers/                  JSON API (/api/*) · 화면 + HTMX 조각 (/, /ui/*)
  templates/                Jinja2 HTML
lms_mock/                   🏫🏛 가상 학교 시스템 (도우미와 독립된 FastAPI 앱)
  moodle.py                 가상 코스모스: /lms/webservice/rest/server.php (Moodle 4.5 규격)
  app.py                    가상 학사정보: /lms/api/* (성적표·졸업 요건·과목)
public/static/              CSS, htmx (Vercel CDN이 직접 제공)
supabase/migrations/        DB 스키마 (3개)
docs/                       진행상황 문서, 예시 교안
```

## 로컬 실행

```bash
cp .env.example .env.local          # 값 채우기 (Git에 올라가지 않음)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000
python -m pytest -q tests          # 챗봇 마무리 처리·마크다운 안전성 테스트
```

| 주소 | 내용 |
|---|---|
| http://localhost:8000 | 화면 |
| http://localhost:8000/api/docs | 도우미 API 문서 |
| http://localhost:8000/lms/api/docs | 가상 학교 시스템 API 문서 |
| http://localhost:8000/api/health | 설정 확인 |

## 환경변수 (모두 서버 전용)

| 이름 | 설명 |
|---|---|
| `SUPABASE_URL` / `SUPABASE_SECRET_KEY` / `SUPABASE_PUBLISHABLE_KEY` | Supabase 주소 / 서버용 비밀 키 / 로그인용 공개 키 |
| `LLM_API_KEY` | OpenAI API 키 |
| `LLM_CHAT_MODEL` | 기본 `gpt-6-luna` |
| `LLM_REASONING_EFFORT` | 기본 `none` (gpt-6-luna 함수 호출에 필요) |
| `LLM_EMBEDDING_MODEL` | 기본 `text-embedding-3-small` (1536차원) |
| `LMS_API_KEY` | 도우미 ↔ 가상 학교 시스템 서버 간 비밀 키 (임의의 긴 문자열) |
| `LMS_API_BASE_URL` | 외부 학사정보 API 주소 (비우면 가상 학사정보) |
| `MOODLE_BASE_URL` | 코스모스(Moodle) 주소 (비우면 가상 코스모스) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | Web Push 알림 키 쌍 / 연락처 — 맥 사파리는 예시 주소(`you@example.com`)를 거절할 수 있으니 **실제 메일 주소나 사이트 https 주소**로 |
| `CRON_SECRET` | 매일 알림 Cron 인증 (Vercel이 `Authorization: Bearer`로 보냄) |
| `SEMESTER_LABEL` / `SEMESTER_START` | 학기 이름 / 1주차 월요일 (예: `2026-08-31`) — '저번 주' → 주차 계산 (선택) |

## 진짜 코스모스로 전환하려면 (학교 허가 후)

1. 학교 전산팀에서 Moodle Web Services 활성화 + 학생별 토큰 발급 (학생 동의 필요)
2. `MOODLE_BASE_URL`을 코스모스 주소로 변경
3. `backend/integrations/lms_client.py`의 `_ws_token`을 저장된(암호화) 학생 토큰을 쓰도록 교체

졸업 요건·전체 성적표는 LMS가 아니라 학사정보시스템에 있으므로 별도 협의가 필요합니다.
