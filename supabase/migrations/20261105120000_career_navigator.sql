-- =====================================================================
-- LMS 3분 학습 도우미 & 커리어 나비게이터 · 마이그레이션 #2
--   (20261105000000_init_lms.sql 적용 후 실행)
--
-- 구조: "가상 LMS"와 "LMS 도우미"를 모듈로 분리한다.
--   ┌ 가상 LMS (lms_ 접두사) ─ 모의 LMS 서버(/lms/api)만 접근. 브라우저 직접 접근 차단
--   │   lms_students, lms_courses, lms_graduation_requirements,
--   │   lms_course_history, lms_materials, lms_assignments, lms_assignment_submissions
--   └ LMS 도우미 ─ AI 에이전트가 MCP 도구 → LMS API 로만 LMS 데이터를 가져온다
--       users(관심 직무·이수 학점·펫·LMS 연결), career_tracks, push_subscriptions, notification_logs
--   두 영역 사이에는 외래키를 두지 않는다 (외부 LMS 연동과 같은 느슨한 결합).
--
-- 원칙: 기존 테이블·데이터는 삭제하지 않음 / 여러 번 실행해도 안전(idempotent)
-- ※ 과목·졸업요건·학생 데이터는 해커톤 시연용 '가상' 데이터이며 실제 학칙이 아님
-- =====================================================================


-- #####################################################################
-- PART A. 가상 LMS
-- #####################################################################

-- ---------------------------------------------------------------------
-- A1. lms_students : 가상 LMS 학생 (학번 기준)
-- ---------------------------------------------------------------------
create table if not exists public.lms_students (
  id              uuid primary key default gen_random_uuid(),
  student_no      text not null unique check (student_no ~ '^[0-9]{8,10}$'),
  name            text not null check (length(trim(name)) > 0),
  department      text not null,
  admission_year  int  not null check (admission_year between 2000 and 2100),
  created_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- A2. lms_courses : 개설 과목 카탈로그
--     category: major_required(전공필수) / major_elective(전공선택)
--               general_required(교양필수) / general_elective(교양선택)
-- ---------------------------------------------------------------------
create table if not exists public.lms_courses (
  code         text primary key check (code ~ '^[A-Z]{2,4}[0-9]{3}$'),
  name         text not null check (length(trim(name)) > 0),
  credits      int  not null check (credits between 1 and 6),
  category     text not null
               check (category in ('major_required', 'major_elective', 'general_required', 'general_elective')),
  year_level   int  not null default 1 check (year_level between 1 and 4),
  skills       text[] not null default '{}',   -- 과목에서 익히는 기술 키워드 (직무 매핑용)
  description  text,
  created_at   timestamptz not null default now()
);

create index if not exists idx_lms_courses_category on public.lms_courses (category);

-- ---------------------------------------------------------------------
-- A3. lms_graduation_requirements : 학과별 졸업 요건
--     자유선택 학점 = total - (전공필수 + 전공선택 + 교양)
-- ---------------------------------------------------------------------
create table if not exists public.lms_graduation_requirements (
  department              text primary key,
  total_credits           int  not null check (total_credits > 0),
  major_required_credits  int  not null default 0 check (major_required_credits >= 0),
  major_elective_credits  int  not null default 0 check (major_elective_credits >= 0),
  general_credits         int  not null default 0 check (general_credits >= 0),
  required_course_codes   text[] not null default '{}',   -- 반드시 이수해야 하는 과목
  note                    text,
  updated_at              timestamptz not null default now(),
  constraint chk_lms_grad_sum check (
    major_required_credits + major_elective_credits + general_credits <= total_credits
  )
);

drop trigger if exists trg_lms_graduation_requirements_updated_at on public.lms_graduation_requirements;
create trigger trg_lms_graduation_requirements_updated_at
  before update on public.lms_graduation_requirements
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------
-- A4. lms_course_history : 수강 이력
--     status: completed(이수) / in_progress(수강 중) / failed(낙제) / dropped(수강 취소)
-- ---------------------------------------------------------------------
create table if not exists public.lms_course_history (
  id           uuid primary key default gen_random_uuid(),
  student_id   uuid not null references public.lms_students (id) on delete cascade,
  course_code  text not null references public.lms_courses (code) on update cascade,
  semester     text not null check (semester ~ '^[0-9]{4}-(1|2|S|W)$'),   -- 예: 2026-1, 2026-S(여름)
  status       text not null default 'completed'
               check (status in ('completed', 'in_progress', 'failed', 'dropped')),
  grade        text check (grade is null or grade in ('A+','A0','B+','B0','C+','C0','D+','D0','F','P','NP')),
  credits      int  not null check (credits between 1 and 6),   -- 수강 당시 학점 (비우면 과목 학점 자동 입력)
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (student_id, course_code, semester),
  constraint chk_lms_course_history_grade_status check (
       (status = 'completed'   and grade is not null and grade not in ('F', 'NP'))
    or (status = 'failed'      and grade in ('F', 'NP'))
    or (status in ('in_progress', 'dropped') and grade is null)
  )
);

create index if not exists idx_lms_course_history_student_status on public.lms_course_history (student_id, status);

drop trigger if exists trg_lms_course_history_updated_at on public.lms_course_history;
create trigger trg_lms_course_history_updated_at
  before update on public.lms_course_history
  for each row execute function public.set_updated_at();

-- 학점을 비워 넣으면 과목 카탈로그의 학점으로 채움 (NOT NULL 검사보다 먼저 실행됨)
create or replace function public.lms_fill_course_history_credits()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.credits is null then
    select c.credits into new.credits from public.lms_courses c where c.code = new.course_code;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_lms_course_history_fill_credits on public.lms_course_history;
create trigger trg_lms_course_history_fill_credits
  before insert or update of course_code, credits on public.lms_course_history
  for each row execute function public.lms_fill_course_history_credits();

-- ---------------------------------------------------------------------
-- A5. lms_materials : LMS에 올라온 주차별 강의 자료(교안)
--     도우미는 MCP 도구로 이 자료를 가져와 RAG 인덱스(documents/document_chunks)에 넣는다.
-- ---------------------------------------------------------------------
create table if not exists public.lms_materials (
  id           uuid primary key default gen_random_uuid(),
  course_code  text not null references public.lms_courses (code) on update cascade on delete cascade,
  week_no      int  not null check (week_no between 1 and 16),
  title        text not null check (length(trim(title)) > 0),
  content      text not null check (length(trim(content)) > 0),
  published_at timestamptz not null default now(),
  unique (course_code, week_no, title)
);

-- ---------------------------------------------------------------------
-- A6. lms_assignments : 과제와 마감일
-- ---------------------------------------------------------------------
create table if not exists public.lms_assignments (
  id           uuid primary key default gen_random_uuid(),
  course_code  text not null references public.lms_courses (code) on update cascade on delete cascade,
  material_id  uuid references public.lms_materials (id) on delete set null,   -- 관련 강의 자료
  title        text not null check (length(trim(title)) > 0),
  description  text,
  due_at       timestamptz not null,
  created_at   timestamptz not null default now(),
  unique (course_code, title)
);

create index if not exists idx_lms_assignments_due on public.lms_assignments (due_at);

-- ---------------------------------------------------------------------
-- A7. lms_assignment_submissions : 과제 제출
-- ---------------------------------------------------------------------
create table if not exists public.lms_assignment_submissions (
  student_id     uuid not null references public.lms_students (id) on delete cascade,
  assignment_id  uuid not null references public.lms_assignments (id) on delete cascade,
  submitted_at   timestamptz not null default now(),
  primary key (student_id, assignment_id)
);

-- ---------------------------------------------------------------------
-- A8. lms_open_assignments (뷰) : 수강 중 과목의 미제출 과제
-- ---------------------------------------------------------------------
create or replace view public.lms_open_assignments
with (security_invoker = true) as
select distinct on (ch.student_id, a.id)
  ch.student_id,
  a.id               as assignment_id,
  a.course_code,
  c.name             as course_name,
  a.material_id,
  a.title,
  a.description,
  a.due_at,
  a.due_at < now()   as is_overdue
from public.lms_course_history ch
join public.lms_assignments a on a.course_code = ch.course_code
join public.lms_courses c     on c.code = a.course_code
where ch.status = 'in_progress'
  and a.due_at > now() - interval '1 day'          -- 마감 후 하루까지는 '지남'으로 표시
  and not exists (
    select 1 from public.lms_assignment_submissions s
     where s.student_id = ch.student_id and s.assignment_id = a.id
  )
order by ch.student_id, a.id, a.due_at;

-- ---------------------------------------------------------------------
-- A9. lms_get_credit_summary : 이수 구분별 취득 학점 (성적표 API용)
-- ---------------------------------------------------------------------
create or replace function public.lms_get_credit_summary(p_student_id uuid)
returns table (category text, earned_credits int, course_count int)
language sql
stable
set search_path = public
as $$
  select c.category,
         coalesce(sum(ch.credits), 0)::int,
         count(*)::int
    from public.lms_course_history ch
    join public.lms_courses c on c.code = ch.course_code
   where ch.student_id = p_student_id
     and ch.status = 'completed'
   group by c.category;
$$;

-- ---------------------------------------------------------------------
-- A10. RLS : 가상 LMS 테이블은 정책 없이 RLS만 켠다
--      → anon/authenticated(브라우저)는 전혀 접근 불가, 모의 LMS 서버(secret 키)만 접근
-- ---------------------------------------------------------------------
alter table public.lms_students                enable row level security;
alter table public.lms_courses                 enable row level security;
alter table public.lms_graduation_requirements enable row level security;
alter table public.lms_course_history          enable row level security;
alter table public.lms_materials               enable row level security;
alter table public.lms_assignments             enable row level security;
alter table public.lms_assignment_submissions  enable row level security;

revoke execute on function public.lms_get_credit_summary(uuid) from anon, authenticated, public;
grant  execute on function public.lms_get_credit_summary(uuid) to service_role;


-- #####################################################################
-- PART B. LMS 도우미 (커리어 나비게이터 · 푸시 알림 · 가상 펫)
-- #####################################################################

-- ---------------------------------------------------------------------
-- B1. career_tracks : 관심 직무별 추천 전공 트랙 (도우미의 커리어 지식)
--     과목 코드는 LMS 과목 코드를 '값'으로만 참조 (모듈 간 외래키 없음)
-- ---------------------------------------------------------------------
create table if not exists public.career_tracks (
  job_role                  text primary key check (job_role ~ '^[a-z_]+$'),
  title                     text not null,
  description               text,
  core_course_codes         text[] not null default '{}',   -- 직무 핵심 과목
  recommended_course_codes  text[] not null default '{}',   -- 있으면 좋은 과목
  key_skills                text[] not null default '{}'
);

-- ---------------------------------------------------------------------
-- B2. users 확장 : LMS 연결, 관심 직무, 이수 학점(LMS 동기화 캐시), 가상 펫
-- ---------------------------------------------------------------------
alter table public.users
  add column if not exists lms_student_id    uuid,                        -- 가상 LMS 학생 ID (외래키 없음)
  add column if not exists lms_synced_at     timestamptz,                 -- 마지막 LMS 동기화 시각
  add column if not exists target_job        text references public.career_tracks (job_role)
                                                  on update cascade on delete set null,
  add column if not exists completed_credits int  not null default 0 check (completed_credits >= 0),
  add column if not exists pet_name          text not null default '나비'
                                                  check (length(pet_name) between 1 and 20);

create unique index if not exists uq_users_lms_student on public.users (lms_student_id)
  where lms_student_id is not null;

-- 학생이 직접 바꿀 수 있는 컬럼만 허용 (이수 학점·LMS 연결·최근 학습 시각 위조 방지)
revoke update on public.users from anon, authenticated;
grant update (display_name, major, target_job, pet_name) on public.users to authenticated;

-- ---------------------------------------------------------------------
-- B3. push_subscriptions : 브라우저 Web Push 구독 정보
--     endpoint/p256dh/auth는 브라우저가 발급. 서버(secret 키)만 읽고 쓴다.
-- ---------------------------------------------------------------------
create table if not exists public.push_subscriptions (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references public.users (id) on delete cascade,
  endpoint         text not null unique check (endpoint ~ '^https://'),
  p256dh           text not null,
  auth             text not null,
  user_agent       text,
  created_at       timestamptz not null default now(),
  last_success_at  timestamptz,
  failure_count    int not null default 0 check (failure_count >= 0)   -- 연속 실패 시 정리
);

create index if not exists idx_push_subscriptions_user on public.push_subscriptions (user_id);

-- ---------------------------------------------------------------------
-- B4. notification_logs : 보낸 알림 기록 (같은 알림을 하루에 두 번 보내지 않기 위함)
--     kind: review_due(망각곡선 복습) / assignment_due(과제 마감) / streak_risk(잔디 끊김 위험)
-- ---------------------------------------------------------------------
create table if not exists public.notification_logs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references public.users (id) on delete cascade,
  kind        text not null check (kind in ('review_due', 'assignment_due', 'streak_risk')),
  ref_key     text not null default '',          -- 예: 과제 ID, 교안 ID
  title       text not null,
  body        text not null,
  local_date  date not null default ((now() at time zone 'Asia/Seoul')::date),
  sent_at     timestamptz not null default now(),
  delivered   int not null default 0 check (delivered >= 0),   -- 성공한 구독(기기) 수
  unique (user_id, kind, ref_key, local_date)
);

create index if not exists idx_notification_logs_user_time on public.notification_logs (user_id, sent_at desc);

-- ---------------------------------------------------------------------
-- B5. RLS
-- ---------------------------------------------------------------------
alter table public.career_tracks      enable row level security;
alter table public.push_subscriptions enable row level security;
alter table public.notification_logs  enable row level security;

drop policy if exists "career_tracks_read_authenticated" on public.career_tracks;
create policy "career_tracks_read_authenticated" on public.career_tracks
  for select to authenticated using (true);

drop policy if exists "notification_logs_select_own" on public.notification_logs;
create policy "notification_logs_select_own" on public.notification_logs
  for select to authenticated using ((select auth.uid()) = user_id);
-- push_subscriptions: 정책 없음 → 서버만 접근 (구독 키 보호)


-- #####################################################################
-- PART C. 시연용 기준 데이터 (다시 실행하면 최신 값으로 갱신)
-- #####################################################################

-- C1. 과목 카탈로그 (총 118학점: 교양 22 / 전공필수 39 / 전공선택 57)
insert into public.lms_courses (code, name, credits, category, year_level, skills, description) values
  ('GE101', '대학글쓰기',               2, 'general_required', 1, '{글쓰기,논리}',                 '보고서·기술 문서 작성 기초'),
  ('GE102', '실용영어회화1',            2, 'general_required', 1, '{영어}',                         '업무 영어 커뮤니케이션 기초'),
  ('GE103', '진로설계와 자기계발',       1, 'general_required', 1, '{진로}',                         '전공 진로 탐색과 포트폴리오'),
  ('GE104', '실용영어회화2',            2, 'general_required', 1, '{영어}',                         '기술 영어 발표와 이메일'),
  ('GE105', '기초수학',                 3, 'general_required', 1, '{수학,논리}',                    '함수·행렬·확률 기초'),
  ('GE201', '창의적 문제해결',          2, 'general_elective', 1, '{문제해결}',                     '디자인 씽킹 기반 문제 해결'),
  ('GE202', 'IT 경영과 창업',           3, 'general_elective', 2, '{기획,창업}',                    'IT 서비스 기획과 비즈니스 모델'),
  ('GE203', '철학과 인공지능',          2, 'general_elective', 2, '{AI 윤리}',                      'AI 시대의 윤리와 책임'),
  ('GE204', '글로벌 문화의 이해',        2, 'general_elective', 1, '{글로벌 역량}',                  '다문화 협업과 커뮤니케이션'),
  ('GE205', '경제학의 이해',            3, 'general_elective', 2, '{경제}',                         '시장과 가격, IT 산업 경제'),
  ('CS101', '프로그래밍 기초(파이썬)',   3, 'major_required',   1, '{Python,알고리즘 기초}',          '변수·제어문·함수·자료형'),
  ('CS102', '컴퓨터 개론',              3, 'major_required',   1, '{컴퓨터 기초}',                   '하드웨어·소프트웨어 기본 개념'),
  ('CS103', '이산수학',                 3, 'major_required',   1, '{논리,집합,그래프 이론}',          '명제 논리, 집합, 관계, 그래프'),
  ('CS104', '컴퓨터 구조',              3, 'major_required',   1, '{CPU,메모리 계층}',               'CPU 구조, 명령어, 메모리 계층'),
  ('CS201', '자료구조',                 3, 'major_required',   1, '{자료구조,Python}',               '스택·큐·리스트·트리·그래프'),
  ('CS202', '데이터베이스',             3, 'major_required',   2, '{SQL,PostgreSQL,ERD}',            '관계형 모델, SQL, 정규화'),
  ('CS203', '운영체제',                 3, 'major_required',   2, '{운영체제,프로세스,메모리}',       '프로세스·스레드·메모리·파일시스템'),
  ('CS204', '컴퓨터 네트워크',          3, 'major_required',   2, '{TCP/IP,HTTP,네트워크}',           'OSI 7계층, TCP/IP, HTTP'),
  ('CS205', '객체지향 설계',            3, 'major_required',   2, '{객체지향,디자인 패턴}',           'SOLID 원칙과 디자인 패턴'),
  ('CS206', '시스템 프로그래밍',        3, 'major_required',   2, '{C,리눅스 시스템 콜}',             'C 언어, 포인터, 시스템 콜'),
  ('CS301', '소프트웨어 공학',          3, 'major_required',   2, '{Git,요구분석,테스트}',            '개발 프로세스, 협업, 테스트'),
  ('CS390', '캡스톤 디자인',            3, 'major_required',   3, '{프로젝트,협업}',                 '팀 프로젝트로 서비스 완성'),
  ('CS391', '현장실습',                 3, 'major_required',   3, '{실무,협업}',                     '산업체 현장 실무 경험'),
  ('CS211', '웹 프로그래밍',            3, 'major_elective',   1, '{HTML,CSS,JavaScript}',            '웹 표준과 브라우저 동작'),
  ('CS212', '자바 프로그래밍',          3, 'major_elective',   1, '{Java,객체지향}',                 '객체지향 프로그래밍'),
  ('CS221', '알고리즘',                 3, 'major_elective',   2, '{알고리즘,코딩테스트}',            '정렬·탐색·그래프·DP'),
  ('CS311', '백엔드 서버 개발',         3, 'major_elective',   2, '{FastAPI,REST API,인증}',          'API 설계, 인증, ORM'),
  ('CS312', '클라우드 컴퓨팅',          3, 'major_elective',   3, '{Docker,AWS,CI/CD}',               '컨테이너와 배포 자동화'),
  ('CS313', '프론트엔드 개발',          3, 'major_elective',   2, '{React,TypeScript}',               '컴포넌트 기반 UI 개발'),
  ('CS314', '모바일 앱 개발',           3, 'major_elective',   2, '{Flutter,모바일 UI}',              '크로스플랫폼 앱 개발'),
  ('CS315', 'UI/UX 디자인',             3, 'major_elective',   2, '{Figma,사용성}',                  '사용자 중심 화면 설계'),
  ('CS321', '머신러닝 기초',            3, 'major_elective',   2, '{머신러닝,scikit-learn}',          '지도·비지도 학습'),
  ('CS322', '데이터 분석',              3, 'major_elective',   2, '{Pandas,시각화,통계}',              '데이터 전처리와 시각화'),
  ('CS323', '딥러닝',                   3, 'major_elective',   3, '{PyTorch,신경망}',                 'CNN·RNN·트랜스포머 기초'),
  ('CS324', '자연어처리와 LLM',          3, 'major_elective',   3, '{NLP,LLM,RAG}',                   '임베딩, LLM 활용, RAG'),
  ('CS331', '정보보안 개론',            3, 'major_elective',   2, '{암호학,보안 기초}',               '보안 3요소, 암호, 인증'),
  ('CS332', '웹 해킹과 시큐어 코딩',     3, 'major_elective',   3, '{OWASP,웹 취약점,시큐어코딩}',      'XSS·SQLi·CSRF 원리와 방어'),
  ('CS333', '네트워크 보안',            3, 'major_elective',   3, '{방화벽,패킷 분석,침입탐지}',       '네트워크 공격과 방어'),
  ('CS334', '디지털 포렌식',            3, 'major_elective',   3, '{포렌식,증거 분석}',               '디지털 증거 수집과 분석'),
  ('CS335', '모의해킹 실습',            3, 'major_elective',   3, '{모의해킹,취약점 진단}',            '합법적 환경에서의 취약점 진단'),
  ('CS341', '리눅스 시스템 관리',       3, 'major_elective',   2, '{Linux,Shell,서버 운영}',           '리눅스 명령어와 서버 운영'),
  ('CS342', 'DevOps 실무',              3, 'major_elective',   3, '{CI/CD,모니터링,IaC}',              '배포 파이프라인과 운영 자동화')
on conflict (code) do update set
  name = excluded.name, credits = excluded.credits, category = excluded.category,
  year_level = excluded.year_level, skills = excluded.skills, description = excluded.description;

-- C2. 졸업 요건: 110학점 = 교양 18 + 전공필수 39 + 전공선택 45 + 자유선택 8
insert into public.lms_graduation_requirements
  (department, total_credits, major_required_credits, major_elective_credits, general_credits, required_course_codes, note)
values
  ('소프트웨어융합과', 110, 39, 45, 18,
   '{CS101,CS102,CS103,CS104,CS201,CS202,CS203,CS204,CS205,CS206,CS301,CS390,CS391,GE101,GE103}',
   '해커톤 시연용 가상 졸업 요건 (실제 학칙 아님). 자유선택 8학점은 어느 과목이든 인정')
on conflict (department) do update set
  total_credits = excluded.total_credits,
  major_required_credits = excluded.major_required_credits,
  major_elective_credits = excluded.major_elective_credits,
  general_credits = excluded.general_credits,
  required_course_codes = excluded.required_course_codes,
  note = excluded.note;

-- C3. 직무 트랙 (도우미)
insert into public.career_tracks (job_role, title, description, core_course_codes, recommended_course_codes, key_skills) values
  ('backend',  '백엔드 개발자',      '서버·API·데이터베이스를 설계하고 운영',
   '{CS202,CS311,CS312,CS341,CS342}', '{CS221,CS204,CS212}',       '{Python,FastAPI,SQL,Docker,Linux,REST API}'),
  ('frontend', '프론트엔드 개발자',  '사용자가 보는 웹 화면과 상호작용을 개발',
   '{CS211,CS313,CS315}',             '{CS314,CS221,CS311}',       '{HTML,CSS,JavaScript,TypeScript,React}'),
  ('ai_data',  'AI·데이터 엔지니어', '데이터를 분석하고 머신러닝·LLM을 서비스에 적용',
   '{CS321,CS322,CS323,CS324}',       '{CS202,CS221,CS312}',       '{Python,Pandas,머신러닝,LLM,SQL}'),
  ('security', '보안 엔지니어',      '시스템·웹·네트워크의 취약점을 찾고 방어',
   '{CS331,CS332,CS333,CS335}',       '{CS334,CS341,CS204,CS203}', '{리눅스,네트워크,웹 취약점,시큐어코딩,모의해킹}')
on conflict (job_role) do update set
  title = excluded.title, description = excluded.description,
  core_course_codes = excluded.core_course_codes,
  recommended_course_codes = excluded.recommended_course_codes,
  key_skills = excluded.key_skills;

-- C4. 강의 자료 (수강 중 과목 위주, RAG 가져오기 시연용)
insert into public.lms_materials (course_code, week_no, title, content) values
  ('CS201', 3, '스택과 큐',
   '스택은 나중에 넣은 데이터가 먼저 나오는 LIFO 구조이며 push, pop, peek 연산을 가진다. '
   '큐는 먼저 넣은 데이터가 먼저 나오는 FIFO 구조이며 enqueue, dequeue 연산을 가진다. '
   '파이썬에서 큐는 collections.deque의 popleft()로 O(1)에 구현한다. '
   '스택은 실행 취소(Undo), 콜 스택, DFS에, 큐는 프린터 대기열, 프로세스 스케줄링, BFS에 쓰인다.'),
  ('CS202', 9, '정규화와 ERD',
   '정규화는 데이터 중복과 이상(삽입·갱신·삭제 이상)을 줄이기 위해 테이블을 분해하는 과정이다. '
   '제1정규형은 모든 속성이 원자값, 제2정규형은 부분 함수 종속 제거, 제3정규형은 이행 함수 종속 제거를 뜻한다. '
   'ERD는 엔터티, 속성, 관계(1:1, 1:N, N:M)를 그림으로 표현하며 N:M 관계는 연결 테이블로 분해한다.'),
  ('CS204', 8, 'TCP 연결 설정과 해제',
   'TCP는 연결 지향 프로토콜로 3-way handshake(SYN → SYN-ACK → ACK)로 연결을 맺는다. '
   '연결 해제는 4-way handshake(FIN → ACK → FIN → ACK)로 이루어지며 TIME_WAIT 상태를 거친다. '
   'TCP는 순서 번호와 확인 응답으로 신뢰성을 보장하고, 흐름 제어와 혼잡 제어를 수행한다.'),
  ('CS311', 7, 'REST API 설계와 FastAPI',
   'REST API는 자원을 URL로 표현하고 HTTP 메서드(GET 조회, POST 생성, PUT/PATCH 수정, DELETE 삭제)로 조작한다. '
   'FastAPI는 타입 힌트와 Pydantic 모델로 요청 본문을 자동 검증하고 OpenAPI 문서를 생성한다. '
   '상태 코드는 200 성공, 201 생성, 400 잘못된 요청, 401 인증 필요, 404 없음, 422 검증 실패를 주로 쓴다.'),
  ('CS332', 6, 'XSS와 CSRF',
   'XSS는 공격자가 삽입한 스크립트가 다른 사용자의 브라우저에서 실행되는 취약점으로, 출력 시 HTML 이스케이프와 CSP로 방어한다. '
   'CSRF는 로그인한 사용자의 브라우저가 의도치 않은 요청을 보내게 만드는 공격으로, SameSite 쿠키, CSRF 토큰, Origin 검사로 방어한다. '
   '토큰은 HttpOnly 쿠키에 저장하면 스크립트로 탈취하기 어렵다.')
on conflict (course_code, week_no, title) do update set content = excluded.content;

-- ---------------------------------------------------------------------
-- C5. lms_seed_demo_student : 시연용 학생·수강 이력·과제를 만들고 학생 ID를 돌려준다
--     모의 LMS 서버(secret 키)만 실행 가능. 다시 실행하면 과제 마감일이 '지금' 기준으로 갱신됨.
--     (도우미는 돌려받은 ID를 users.lms_student_id 에 저장해 LMS 계정을 연결한다)
-- ---------------------------------------------------------------------
create or replace function public.lms_seed_demo_student(
  p_student_no  text,
  p_name        text,
  p_department  text default '소프트웨어융합과'
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_student_id uuid;
begin
  insert into public.lms_students (student_no, name, department, admission_year)
  values (p_student_no, p_name, p_department, 2025)
  on conflict (student_no) do update set name = excluded.name
  returning id into v_student_id;

  insert into public.lms_course_history (student_id, course_code, semester, status, grade)
  select v_student_id, v.code, v.semester, v.status, v.grade
    from (values
      ('GE101', '2025-1', 'completed',   'A0'),
      ('GE102', '2025-1', 'completed',   'B+'),
      ('GE105', '2025-1', 'completed',   'B0'),
      ('CS101', '2025-1', 'completed',   'A+'),
      ('CS102', '2025-1', 'completed',   'B0'),
      ('CS103', '2025-1', 'completed',   'C+'),
      ('CS211', '2025-1', 'completed',   'B+'),
      ('GE103', '2025-2', 'completed',   'P'),
      ('GE104', '2025-2', 'completed',   'B0'),
      ('CS104', '2025-2', 'completed',   'B+'),
      ('CS201', '2025-2', 'completed',   'A0'),
      ('CS212', '2025-2', 'completed',   'C+'),
      ('CS203', '2025-2', 'failed',      'F'),
      ('GE201', '2026-1', 'completed',   'A0'),
      ('CS205', '2026-1', 'completed',   'B+'),
      ('CS206', '2026-1', 'completed',   'B0'),
      ('CS221', '2026-1', 'completed',   'B+'),
      ('CS331', '2026-1', 'completed',   'A0'),
      ('CS202', '2026-2', 'in_progress', null),
      ('CS204', '2026-2', 'in_progress', null),
      ('CS311', '2026-2', 'in_progress', null),
      ('CS332', '2026-2', 'in_progress', null)
    ) as v(code, semester, status, grade)
  on conflict (student_id, course_code, semester) do update set
    status = excluded.status, grade = excluded.grade;

  insert into public.lms_assignments (course_code, material_id, title, description, due_at)
  select v.course_code,
         (select m.id from public.lms_materials m
           where m.course_code = v.course_code order by m.week_no desc limit 1),
         v.title, v.description, now() + v.due_in
    from (values
      ('CS311', 'FastAPI CRUD API 구현',      '게시판 CRUD API와 Pydantic 검증 구현',          interval '6 hours'),
      ('CS202', '수강신청 시스템 ERD 설계',    '엔터티·관계·정규화(3NF)까지 반영한 ERD 제출',    interval '1 day'),
      ('CS204', 'TCP 3-way handshake 정리',   'SYN/SYN-ACK/ACK 과정과 상태 전이 다이어그램',   interval '3 days'),
      ('CS332', 'XSS 취약점 분석 보고서',      '실습 환경의 반사형 XSS 원인 분석과 방어 코드',   interval '5 days')
    ) as v(course_code, title, description, due_in)
  on conflict (course_code, title) do update set
    material_id = excluded.material_id, description = excluded.description, due_at = excluded.due_at;

  return v_student_id;
end;
$$;

revoke execute on function public.lms_seed_demo_student(text, text, text) from anon, authenticated, public;
grant  execute on function public.lms_seed_demo_student(text, text, text) to service_role;

-- =====================================================================
-- 실행 확인용 (선택)
-- select code, name, credits, category from public.lms_courses order by code;
-- select * from public.lms_graduation_requirements;
-- select * from public.career_tracks;
-- =====================================================================
