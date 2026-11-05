# LMS 3분 학습 도우미 & 커리어 나비게이터

RAG + MCP 기반 학습·진로 도우미 (해커톤 「내 전공 현장을 바꾸는 AI」 출품작)
**파이썬 하나로** 화면(서버 렌더링), API, AI 에이전트, 가상 학교 시스템을 모두 만듭니다.

- **챗봇**: AI 에이전트가 MCP 도구로 교안·LMS 데이터를 찾아 출처와 함께 답변 + 3분 요약
- **마이크로 퀴즈**: 객관식·O/X·코드 빈칸 3문제 생성, 서버 채점
- **복습 알림 · 학습 잔디**: 에빙하우스 망각 곡선(1·3·7일), GitHub식 1년 히트맵
- **가상 코스모스 연동**: Moodle 4.5 Web Services 규격의 모의 LMS (과제·마감·강의 자료)
- **푸시 알림 · 가상 펫**: 매일 저녁 하루 한 번 마감·복습 알림, 연속 학습에 따라 자라는 펫
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
  mcp_server.py             MCP 서버 (@mcp.tool 도구 8개)
  integrations/             학교 시스템 연동: 게이트웨이, 코스모스 어댑터, Moodle 클라이언트
  services/                 에이전트, 커리어, LMS 연결, 퀴즈, 복습, 잔디, 교안, 임베딩
  routers/                  JSON API (/api/*) · 화면 + HTMX 조각 (/, /ui/*)
  templates/                Jinja2 HTML
lms_mock/                   🏫🏛 가상 학교 시스템 (도우미와 독립된 FastAPI 앱)
  moodle.py                 가상 코스모스: /lms/webservice/rest/server.php (Moodle 4.5 규격)
  app.py                    가상 학사정보: /lms/api/* (성적표·졸업 요건·과목)
public/static/              CSS, htmx (Vercel CDN이 직접 제공)
supabase/migrations/        DB 스키마 (2개)
docs/                       진행상황 문서, 예시 교안
```

## 로컬 실행

```bash
cp .env.example .env.local          # 값 채우기 (Git에 올라가지 않음)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000
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
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | Web Push 알림 키 쌍 / 연락처(mailto:) |
| `CRON_SECRET` | 매일 알림 Cron 인증 (Vercel이 `Authorization: Bearer`로 보냄) |

## 진짜 코스모스로 전환하려면 (학교 허가 후)

1. 학교 전산팀에서 Moodle Web Services 활성화 + 학생별 토큰 발급 (학생 동의 필요)
2. `MOODLE_BASE_URL`을 코스모스 주소로 변경
3. `backend/integrations/lms_client.py`의 `_ws_token`을 저장된(암호화) 학생 토큰을 쓰도록 교체

졸업 요건·전체 성적표는 LMS가 아니라 학사정보시스템에 있으므로 별도 협의가 필요합니다.
