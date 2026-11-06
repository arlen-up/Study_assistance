-- =====================================================================
-- 사용자 이름(닉네임)
--   가입 후 처음 들어오면 화면에서 정한다 (backend/services/profile_service.py).
--   · 2~16자, 한글·영어·숫자·밑줄(_)만, 밑줄만으로는 안 됨
--   · 다른 사람과 겹치면 안 됨 (영어 대소문자는 같은 글자로 봄: Kim = kim)
--   형식과 중복은 앱에서도 검사하지만, DB에서도 막아서 우회할 수 없게 한다.
--   다시 실행해도 안전하다.
-- =====================================================================

alter table public.users add column if not exists username text;

alter table public.users drop constraint if exists users_username_format;
alter table public.users add constraint users_username_format check (
  username is null
  or (username ~ '^[0-9A-Za-z가-힣_]{2,16}$' and username ~ '[0-9A-Za-z가-힣]')
);

create unique index if not exists uq_users_username_lower on public.users (lower(username));

-- 이름이 이미 쓰이고 있는지 (본인 것은 빼고). 서버(secret 키)에서만 부른다.
create or replace function public.is_username_taken(p_username text, p_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.users
     where lower(username) = lower(p_username)
       and id <> p_user_id
  );
$$;

revoke all on function public.is_username_taken(text, uuid) from public, anon, authenticated;

-- 학생이 브라우저용 키로 username을 직접 바꾸지 못하게 그대로 둔다
-- (update 허용 칸: display_name, major, target_job, pet_name — 20261105120000 마이그레이션)
