# LMS 3분 학습 도우미

RAG + MCP 기반 가상 LMS 학습 도우미 (해커톤 「내 전공 현장을 바꾸는 AI」 출품작)
**파이썬 하나로** 화면(서버 렌더링)과 API, AI 에이전트를 모두 만듭니다.

- **챗봇**: AI 에이전트가 MCP 도구로 교안을 검색해 출처와 함께 답변 + 3분 요약
- **마이크로 퀴즈**: 오늘 교안 키워드로 객관식·O/X·코드 빈칸 3문제 생성, 서버 채점
- **복습 알림**: 에빙하우스 망각 곡선(1·3·7일) 기반 앱 내 알림
- **학습 잔디**: GitHub식 1년 히트맵과 연속 학습일

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| 화면 | FastAPI + Jinja2 템플릿 + HTMX(화면 일부 교체) + 순수 CSS |
| API | FastAPI, Pydantic |
| AI | LangChain + MCP Python SDK, OpenAI 호환 API |
| DB·로그인 | Supabase (PostgreSQL, pgvector, Auth) |
| 배포 | GitHub + Vercel (Python 서버리스 함수) |

## 폴더 구조

```
api/index.py              Vercel 진입점 (backend.app 불러오기)
backend/
  app.py                  앱 조립: 라우터·오류 처리·보안 미들웨어
  mcp_server.py           MCP 서버 (@mcp.tool 도구 3개)
  core/                   설정, Supabase 연결, 토큰 검증, 오류 정의
  routers/api.py          JSON API (/api/*, Swagger: /api/docs)
  routers/web.py          화면 (/, /login) + HTMX 조각 (/ui/*)
  services/               에이전트, 임베딩, 교안, 퀴즈, 복습, 잔디
  web/                    로그인 쿠키 세션, 템플릿 헬퍼
  templates/              Jinja2 HTML
public/static/            CSS, htmx (Vercel CDN이 직접 제공)
supabase/migrations/      DB 스키마
docs/진행상황.md           초보자용 진행 기록
```

## 로컬 실행

```bash
cp .env.example .env.local          # 값 채우기 (Git에 올라가지 않음)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000
```

- 화면: http://localhost:8000
- API 문서: http://localhost:8000/api/docs
- 상태 확인: http://localhost:8000/api/health

## 환경변수 (모두 서버 전용)

| 이름 | 설명 |
|---|---|
| `SUPABASE_URL` | Supabase 프로젝트 주소 |
| `SUPABASE_SECRET_KEY` | 서버용 비밀 키 (RLS 우회) |
| `SUPABASE_PUBLISHABLE_KEY` | 로그인·토큰 갱신용 공개 키 (권장) |
| `LLM_API_KEY` | OpenAI 호환 API 키 |
| `LLM_BASE_URL` | 기본 `https://api.openai.com/v1` |
| `LLM_CHAT_MODEL` | 기본 `gpt-4o-mini` |
| `LLM_EMBEDDING_MODEL` | 기본 `text-embedding-3-small` (1536차원) |
