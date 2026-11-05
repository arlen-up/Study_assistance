-- =====================================================================
-- RAG 기반 가상 LMS 3분 학습 도우미 · Step 1 Supabase 스키마
-- Supabase SQL Editor에 전체 붙여넣기 → Run
-- 여러 번 실행해도 안전하도록(idempotent) 작성됨
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. 익스텐션
-- ---------------------------------------------------------------------
create extension if not exists vector with schema extensions;   -- pgvector (임베딩 저장·검색)
create extension if not exists pgcrypto with schema extensions; -- gen_random_uuid()

-- ---------------------------------------------------------------------
-- 공통: updated_at 자동 갱신 트리거 함수
-- ---------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------
-- 1. users : 학습자 프로필
--    Supabase Auth(auth.users)와 1:1. 로그인 정보는 Auth가 관리하고
--    여기에는 전공·최근 학습 시각(망각 곡선 기준점) 등 앱 데이터만 보관
-- ---------------------------------------------------------------------
create table if not exists public.users (
  id              uuid primary key references auth.users (id) on delete cascade,
  email           text,
  display_name    text,
  major           text not null default 'IT',
  last_studied_at timestamptz,                         -- 마지막 학습 완료 시각
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

drop trigger if exists trg_users_updated_at on public.users;
create trigger trg_users_updated_at
  before update on public.users
  for each row execute function public.set_updated_at();

-- 회원가입 시 프로필 자동 생성
create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.users (id, email, display_name)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data ->> 'name', split_part(new.email, '@', 1))
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_auth_user();

-- 이미 가입된 사용자 프로필 보충(backfill)
insert into public.users (id, email, display_name)
select u.id, u.email, coalesce(u.raw_user_meta_data ->> 'name', split_part(u.email, '@', 1))
from auth.users u
on conflict (id) do nothing;

-- ---------------------------------------------------------------------
-- 2. documents : 가상 LMS 교안 원문
--    MCP tool `fetch_lms_document`가 파싱한 교안 1건 = 1행
-- ---------------------------------------------------------------------
create table if not exists public.documents (
  id           uuid primary key default gen_random_uuid(),
  title        text not null check (length(trim(title)) > 0),
  course_name  text,
  week_no      int check (week_no is null or week_no > 0),
  source_type  text not null default 'text'
               check (source_type in ('pdf', 'markdown', 'html', 'text')),
  source_url   text,
  raw_text     text,                                   -- 파싱된 전체 텍스트
  keywords     text[] not null default '{}',           -- 교안 핵심 키워드
  created_by   uuid references public.users (id) on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index if not exists idx_documents_course_week on public.documents (course_name, week_no);
create index if not exists idx_documents_created_at on public.documents (created_at desc);

drop trigger if exists trg_documents_updated_at on public.documents;
create trigger trg_documents_updated_at
  before update on public.documents
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------
-- 3. document_chunks : RAG 검색 단위(청크) + 1536차원 임베딩
--    MCP tool `query_vector_db`가 코사인 유사도로 검색하는 대상
--    ※ 사용할 임베딩 모델의 출력 차원이 1536인지 반드시 확인
-- ---------------------------------------------------------------------
create table if not exists public.document_chunks (
  id           uuid primary key default gen_random_uuid(),
  document_id  uuid not null references public.documents (id) on delete cascade,
  chunk_index  int not null check (chunk_index >= 0),
  content      text not null check (length(trim(content)) > 0),
  page_number  int check (page_number is null or page_number > 0),  -- 출처 표시용
  token_count  int check (token_count is null or token_count >= 0),
  metadata     jsonb not null default '{}'::jsonb,
  embedding    extensions.vector(1536),
  created_at   timestamptz not null default now(),
  unique (document_id, chunk_index)
);

create index if not exists idx_chunks_document on public.document_chunks (document_id);

-- HNSW 벡터 인덱스 (코사인 거리)
create index if not exists idx_chunks_embedding_hnsw
  on public.document_chunks
  using hnsw (embedding extensions.vector_cosine_ops)
  with (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------
-- 4. quizzes : AI가 생성한 적응형 마이크로 퀴즈 세트
--    questions 예시(JSON 배열, 기본 3문제):
--    [{"type":"multiple_choice","question":"...","options":["A","B","C","D"],
--      "answer":"B","explanation":"...","source_chunk_id":"uuid"},
--     {"type":"ox","question":"...","answer":"O","explanation":"..."},
--     {"type":"code_blank","question":"...","code":"def f(): ____","answer":"return","explanation":"..."}]
-- ---------------------------------------------------------------------
create table if not exists public.quizzes (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references public.users (id) on delete cascade,
  document_id  uuid not null references public.documents (id) on delete cascade,
  keywords     text[] not null default '{}',
  questions    jsonb not null
               check (
                 case when jsonb_typeof(questions) = 'array'
                      then jsonb_array_length(questions) between 1 and 10
                      else false
                 end
               ),
  difficulty   text not null default 'medium'
               check (difficulty in ('easy', 'medium', 'hard')),
  model_name   text,
  created_at   timestamptz not null default now()
);

create index if not exists idx_quizzes_user_created on public.quizzes (user_id, created_at desc);
create index if not exists idx_quizzes_document on public.quizzes (document_id);

-- ---------------------------------------------------------------------
-- 5. study_logs : 학습(퀴즈) 완료 기록
--    잔디(스트릭) 히트맵 + 에빙하우스 복습 시점 계산의 원천 데이터
-- ---------------------------------------------------------------------
create table if not exists public.study_logs (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references public.users (id) on delete cascade,
  document_id       uuid not null references public.documents (id) on delete cascade,
  quiz_id           uuid references public.quizzes (id) on delete set null,
  score             int check (score is null or score >= 0),
  total_questions   int check (total_questions is null or total_questions > 0),
  completed         boolean not null default true,
  duration_seconds  int check (duration_seconds is null or duration_seconds >= 0),
  studied_at        timestamptz not null default now(),
  constraint chk_score_le_total
    check (score is null or total_questions is null or score <= total_questions)
);

create index if not exists idx_study_logs_user_time
  on public.study_logs (user_id, studied_at desc);
create index if not exists idx_study_logs_user_doc_time
  on public.study_logs (user_id, document_id, studied_at desc);

-- 학습 완료 시 users.last_studied_at 자동 갱신
create or replace function public.update_last_studied_at()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.completed then
    update public.users
       set last_studied_at = greatest(coalesce(last_studied_at, new.studied_at), new.studied_at)
     where id = new.user_id;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_study_logs_last_studied on public.study_logs;
create trigger trg_study_logs_last_studied
  after insert on public.study_logs
  for each row execute function public.update_last_studied_at();

-- =====================================================================
-- RPC 함수 / 뷰 (supabase-py에서 .rpc() / .table() 으로 호출)
-- =====================================================================

-- ---------------------------------------------------------------------
-- A. 벡터 유사도 검색 (MCP query_vector_db 용)
-- ---------------------------------------------------------------------
create or replace function public.match_document_chunks(
  query_embedding     extensions.vector(1536),
  match_count         int default 5,
  match_threshold     float default 0.0,
  filter_document_id  uuid default null
)
returns table (
  chunk_id        uuid,
  document_id     uuid,
  document_title  text,
  chunk_index     int,
  page_number     int,
  content         text,
  metadata        jsonb,
  similarity      float
)
language sql
stable
set search_path = public, extensions
as $$
  select
    c.id,
    c.document_id,
    d.title,
    c.chunk_index,
    c.page_number,
    c.content,
    c.metadata,
    1 - (c.embedding <=> query_embedding) as similarity
  from public.document_chunks c
  join public.documents d on d.id = c.document_id
  where c.embedding is not null
    and (filter_document_id is null or c.document_id = filter_document_id)
    and 1 - (c.embedding <=> query_embedding) >= match_threshold
  order by c.embedding <=> query_embedding
  limit greatest(1, least(match_count, 50));
$$;

-- ---------------------------------------------------------------------
-- B. 잔디 히트맵: 최근 N일(기본 365) 날짜별 학습 여부 배열 (KST 기준)
-- ---------------------------------------------------------------------
create or replace function public.get_study_heatmap(
  p_user_id  uuid,
  p_days     int default 365
)
returns table (
  day            date,
  is_completed   boolean,
  session_count  int
)
language sql
stable
set search_path = public
as $$
  with bounds as (
    select
      (now() at time zone 'Asia/Seoul')::date                                         as today,
      (now() at time zone 'Asia/Seoul')::date - (greatest(1, least(p_days, 366)) - 1) as start_day
  ),
  days as (
    select gs::date as d
    from bounds b,
         generate_series(b.start_day, b.today, interval '1 day') as gs
  ),
  logs as (
    select (l.studied_at at time zone 'Asia/Seoul')::date as d,
           count(*)::int                                  as cnt
    from public.study_logs l, bounds b
    where l.user_id = p_user_id
      and l.completed
      and l.studied_at >= (b.start_day::timestamp at time zone 'Asia/Seoul')
    group by 1
  )
  select days.d,
         coalesce(logs.cnt, 0) > 0,
         coalesce(logs.cnt, 0)
  from days
  left join logs on logs.d = days.d
  order by days.d;
$$;

-- ---------------------------------------------------------------------
-- C. 에빙하우스 복습 일정 뷰 (교안별)
--    완료 1회차 → +1일, 2회차 → +3일, 3회차 → +7일, 4회차 이상 → 완료(null)
-- ---------------------------------------------------------------------
create or replace view public.review_schedule
with (security_invoker = true) as
with stats as (
  select l.user_id,
         l.document_id,
         max(l.studied_at)  as last_studied_at,
         count(*)::int      as review_count
  from public.study_logs l
  where l.completed
  group by l.user_id, l.document_id
)
select
  s.user_id,
  s.document_id,
  d.title                                   as document_title,
  s.last_studied_at,
  s.review_count,
  s.last_studied_at + interval '1 day'      as review_1d_at,
  s.last_studied_at + interval '3 days'     as review_3d_at,
  s.last_studied_at + interval '7 days'     as review_7d_at,
  n.next_review_at,
  coalesce(n.next_review_at <= now(), false) as is_due
from stats s
join public.documents d on d.id = s.document_id
cross join lateral (
  select case s.review_count
           when 1 then s.last_studied_at + interval '1 day'
           when 2 then s.last_studied_at + interval '3 days'
           when 3 then s.last_studied_at + interval '7 days'
           else null
         end as next_review_at
) n;

-- =====================================================================
-- RLS (행 수준 보안)
--  - 프론트(anon/authenticated 키): 본인 데이터만 접근
--  - 백엔드(service_role 키): RLS 우회 → 교안 업로드·임베딩 저장
--    ※ service_role 키는 서버(Vercel 환경변수, NEXT_PUBLIC_ 아님)에만 저장
-- =====================================================================
alter table public.users           enable row level security;
alter table public.documents       enable row level security;
alter table public.document_chunks enable row level security;
alter table public.quizzes         enable row level security;
alter table public.study_logs      enable row level security;

-- users
drop policy if exists "users_select_own" on public.users;
create policy "users_select_own" on public.users
  for select to authenticated using ((select auth.uid()) = id);

drop policy if exists "users_update_own" on public.users;
create policy "users_update_own" on public.users
  for update to authenticated
  using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

-- documents / chunks: 로그인 사용자는 읽기만
drop policy if exists "documents_read_authenticated" on public.documents;
create policy "documents_read_authenticated" on public.documents
  for select to authenticated using (true);

drop policy if exists "chunks_read_authenticated" on public.document_chunks;
create policy "chunks_read_authenticated" on public.document_chunks
  for select to authenticated using (true);

-- quizzes
drop policy if exists "quizzes_select_own" on public.quizzes;
create policy "quizzes_select_own" on public.quizzes
  for select to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "quizzes_insert_own" on public.quizzes;
create policy "quizzes_insert_own" on public.quizzes
  for insert to authenticated with check ((select auth.uid()) = user_id);

-- study_logs
drop policy if exists "study_logs_select_own" on public.study_logs;
create policy "study_logs_select_own" on public.study_logs
  for select to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "study_logs_insert_own" on public.study_logs;
create policy "study_logs_insert_own" on public.study_logs
  for insert to authenticated with check ((select auth.uid()) = user_id);

-- 익명(anon) 사용자는 RPC 실행 불가
revoke execute on function public.match_document_chunks(extensions.vector, int, float, uuid) from anon, public;
revoke execute on function public.get_study_heatmap(uuid, int) from anon, public;
grant  execute on function public.match_document_chunks(extensions.vector, int, float, uuid) to authenticated, service_role;
grant  execute on function public.get_study_heatmap(uuid, int) to authenticated, service_role;

-- =====================================================================
-- 실행 확인용 (선택)
-- select * from public.get_study_heatmap(auth.uid(), 7);
-- select * from public.review_schedule;
-- =====================================================================
