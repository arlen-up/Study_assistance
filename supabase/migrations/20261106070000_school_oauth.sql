-- =====================================================================
-- 학교 계정 연결 (OAuth 2.0 Authorization Code + PKCE) — 가상 코스모스
--
--   예전: 도우미 서버가 LMS_API_KEY 하나로 '아무 학생'의 토큰을 계산·발급받았다 (만능 키, 본인 확인 없음).
--   지금: 학생이 학교(가상 코스모스)에 직접 로그인해 동의해야만, 그 학생 전용 토큰이 발급된다.
--     · lms_students.password_hash : 가상 학교 로그인 비밀번호 (scrypt 해시, 원문 저장 안 함)
--     · lms_oauth_codes            : 일회용 코드 (해시만 저장, 5분, 한 번만 사용, PKCE 값 함께 저장)
--     · lms_access_tokens          : 학생 전용 토큰 (해시만 저장, 권한 범위·만료·폐기)
--     · users.lms_token_enc        : 도우미가 받은 토큰 (서버 키로 암호화해 저장)
--   가상 학교 테이블(lms_*)은 브라우저용 키로 접근할 수 없다 (서버 secret 키만).
--   다시 실행해도 안전하다.
-- =====================================================================

alter table public.lms_students add column if not exists password_hash text;

create table if not exists public.lms_oauth_codes (
  code_hash       text primary key,                 -- sha256(코드) — 원문은 저장하지 않는다
  student_id      uuid not null references public.lms_students (id) on delete cascade,
  client_id       text not null,
  redirect_uri    text not null,
  code_challenge  text not null,                    -- PKCE S256
  scope           text not null,
  expires_at      timestamptz not null,
  used_at         timestamptz,
  created_at      timestamptz not null default now()
);

create table if not exists public.lms_access_tokens (
  token_hash   text primary key,                    -- sha256(토큰) — 원문은 저장하지 않는다
  student_id   uuid not null references public.lms_students (id) on delete cascade,
  client_id    text not null,
  scope        text not null,                       -- 공백으로 구분: lms.read academic.read lms.submit
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null,
  revoked_at   timestamptz
);
create index if not exists idx_lms_access_tokens_student on public.lms_access_tokens (student_id);

alter table public.lms_oauth_codes   enable row level security;
alter table public.lms_access_tokens enable row level security;
revoke all on public.lms_oauth_codes   from anon, authenticated;
revoke all on public.lms_access_tokens from anon, authenticated;
revoke select (password_hash) on public.lms_students from anon, authenticated;

-- 일회용 코드 사용: 확인과 '사용됨' 표시를 한 번에 (같은 코드를 두 번 쓰는 재사용 공격 방지)
create or replace function public.lms_consume_oauth_code(p_code_hash text)
returns table (student_id uuid, client_id text, redirect_uri text, code_challenge text, scope text)
language sql
volatile
security definer
set search_path = ''
as $$
  update public.lms_oauth_codes c
     set used_at = now()
   where c.code_hash = p_code_hash
     and c.used_at is null
     and c.expires_at > now()
  returning c.student_id, c.client_id, c.redirect_uri, c.code_challenge, c.scope;
$$;
revoke all on function public.lms_consume_oauth_code(text) from public, anon, authenticated;

-- 도우미: 학교가 발급한 토큰을 암호화해 보관 (브라우저용 키로는 바꿀 수 없음 — update 허용 칸에 없음)
alter table public.users add column if not exists lms_token_enc text;
