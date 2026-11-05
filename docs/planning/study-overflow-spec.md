# Role & Objective
너는 10년 차 이상의 시니어 파이썬 풀스택 개발자이자 AI 아키텍트야. 우리는 "내 전공 현장을 바꾸는 AI"를 주제로 하는 무박 2일 해커톤에 출품할 『RAG 기반 가상 LMS 3분 학습 도우미』 앱을 개발할 거야.
단순한 예제 코드가 아닌, 즉시 실행 가능하고 에러 핸들링과 타입 힌팅(Pydantic)이 완벽히 적용된 프로덕션 레벨의 코드를 작성해야 해. 코드를 생략(`...`)하지 말고 전체를 작성해 줘.

# 1. Tech Stack & Environment
- **Frontend:** Next.js (App Router), Tailwind CSS, shadcn/ui, `react-markdown` (마크다운 렌더링), `react-pdf` (PDF 뷰어), Mermaid.js (다이어그램)
- **Backend (Core):** Python 3.11+, FastAPI, **MCP (Model Context Protocol) Python SDK**
- **Database & Auth:** Supabase (PostgreSQL, `pgvector`, `supabase-py`)
- **AI / RAG:** LangChain, Tilon 멀티 AI API (OpenAI 호환 API)

# 2. UI/UX 및 대시보드 구조 (Frontend)
1. **레이아웃 배치:** 사용자가 가장 원하는 핵심 기능(챗봇, PDF 뷰어)을 메인 화면 중앙에 크게 배치해. 부가 정보(커리어 나비게이터 등)와 대시보드는 상단 탭이나 버튼으로 빼서 깔끔하게 구성해.
2. **대시보드 시각화:** 상단 대시보드에는 학생의 학습 데이터(출석, 퀴즈 정답률, 과제 달성도 등)를 4개의 그래프 형태로 시각화해 줘.
3. **PDF 뷰어:** 메인 화면 한쪽 빈 공간에 교안 원문을 띄울 수 있는 PDF 뷰어 컴포넌트를 배치해.
4. **알림 시스템 (Cross-Platform):** 맥/윈도우 호환을 위해 Web Push 기반으로 작성하되, 주석으로 메일 송신 및 카카오톡 연동 확장 가능성을 명시해 줘.

# 3. 통합 챗봇 & 라우터 분기 로직 (Core Backend)
마인드맵, 카드, 퀴즈 등 파편화된 기능을 없애고 **모든 상호작용을 단일 챗봇 창에서 해결**하도록 통합해. (예: "저번 주 자바 내용 알려줘", "오늘 배운 내용 퀴즈 내줘" 모두 챗봇이 처리).

사용자 질문이 들어오면 LLM이 의도를 파악하여 **3가지 Type**으로 라우팅(`/api/chat`)하는 분기 로직을 작성해.
- **Type A (study - 학습 질문):**
  - RAG(문서 검색 + 답변)를 수행해.
  - 교안에 없는 내용을 질문하면 반드시 **"[근거 부족]"**이라는 문구를 출력해.
  - 답변 생성 시 **어떤 교안의 몇 페이지를 참고했는지 맨 아래에 출처를 반드시 명시**해.
  - 설명에 시각화가 필요할 경우(예: ERD, 아키텍처) 답변 하단에 Mermaid.js 코드 블록을 생성해 프론트에서 다이어그램으로 렌더링되게 해.
- **Type B (info - 학적 정보 조회):**
  - MCP Tool을 호출하여 학생의 전공 과정, 이수 학점, 수강 중인 강의 정보를 DB에서 파악 후 답변해. 이 타입에서는 "[근거 부족]"을 띄우지 마.
- **Type C (chat - 일상 대화):**
  - 일반적인 챗봇처럼 자연스럽게 대답해. 절대 "[근거 부족]"이라는 문구를 띄우지 마.

# 4. 프론트엔드 챗봇 UI 요구사항
- 화면에 표시되는 답변에서 `session_id` 같은 시스템 변수는 렌더링하지 마.
- 마크다운 문법과 다이어그램(Mermaid) 렌더링을 완벽히 지원해.
- **Tool Calling 가시화:** AI가 MCP 도구를 사용(예: 학적 조회, 벡터 DB 검색)할 경우, 챗봇 말풍선 위에 작은 토글 버튼(UI)을 띄워. 사용자가 클릭하면 도구의 Input / Output 로그를 볼 수 있어야 해.

# 5. Execution Steps (이 순서대로 나와 대화하며 하나씩 완벽히 해결해 줘)

**[Step 1] Supabase Database Schema & 시연용 데모 계정 세팅 (SQL)**
- 필수 테이블: `users` (전공, 이수 학점 포함), `documents`, `document_chunks` (pgvector), `study_logs`, `courses` (수강 강의 내역).
- **중요:** 해커톤 시연을 위해, 5개의 학생 데모 계정과 각 계정별 수강 중인 수업, 학점 정보 더미 데이터를 `INSERT`하는 SQL 구문도 포함해 줘.

**[Step 2] Project Architecture & Vercel Config**
- Step 1 완료 후, Next.js와 Python 백엔드가 공존하는 Vercel 모노레포 폴더 트리를 시각화하고 `vercel.json` 및 `requirements.txt`를 작성해 줘.

**[Step 3] FastAPI Backend & MCP Router Implementation**
- `api/routers/chat.py` 내에 `chat`, `study`, `info` Type을 분류하고 각각 다르게 처리하는 핵심 라우터 로직을 작성해 줘.
- `api/mcp_server.py`에 학생 정보를 가져오는 툴, RAG 검색 툴을 정의해.

**[Step 4] Frontend Integration (Next.js)**
- 메인 화면 (`app/page.tsx`)에 PDF 뷰어와 마크다운/다이어그램/Tool Call 토글이 지원되는 통합 챗봇 컴포넌트를 배치하는 코드를 작성해 줘.

**지금 바로 [Step 1] Supabase Database Schema (SQL) 전체 코드를 작성해 줘. 내가 승인하면 Step 2로 넘어가자.**
