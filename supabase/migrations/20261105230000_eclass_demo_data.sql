-- =====================================================================
-- 시연 데이터 교체: 실제 한양여대 e-class 2026-2학기 시간표 기반
-- ---------------------------------------------------------------------
-- 무엇을 바꾸나요?
--   - 시연 학생의 '이번 학기(2026-2) 수강 과목'을 실제 e-class 6과목으로 교체
--   - 실제 주차별 강의 주제·과제 이름을 가상 코스모스 자료/과제로 추가
-- 넣지 않은 것 (개인정보):
--   - 학번, 이름, 성적, 제출 여부, e-class 강좌 ID·주소
-- 그대로인 것:
--   - 이전 학기 이수 이력(45학점), 졸업 요건(110학점, 시연용 가상 기준)
--   - 과목 코드(CS·SW·GE + 숫자)는 시연용으로 붙인 것 (실제 학수번호 아님)
-- 다시 실행해도 안전합니다 (upsert).
-- =====================================================================

-- 1. 과목 카탈로그
--    기존 CS202·CS204는 같은 분야의 실제 과목 이름으로 바꾸고, 나머지 4과목은 새로 추가
insert into public.lms_courses (code, name, credits, category, year_level, skills, description) values
  ('CS202', '데이터베이스기본',                 3, 'major_required',   2, '{SQL,PostgreSQL,ERD}',               '관계형 모델, SQL, 정규화'),
  ('CS204', '정보통신개론',                     3, 'major_required',   2, '{TCP/IP,통신 프로토콜,네트워크}',     '정보 전송 시스템, 통신 프로토콜, 근거리통신망'),
  ('SW111', '컴퓨터공학활용',                   3, 'major_elective',   1, '{Git,개발 도구}',                     'Git·개발 도구로 포트폴리오 프로그램 관리'),
  ('SW221', '파이선프로그래밍실무',             3, 'major_elective',   2, '{Python,데이터 수집,데이터 분석}',    '파이썬으로 데이터 수집과 AI·데이터 분석'),
  ('SW231', '자바프로그래밍실무',               3, 'major_elective',   2, '{Java,객체지향,예외처리,쓰레드}',     '상속·인터페이스·예외처리·쓰레드·제네릭'),
  ('GE301', 'AI로 완성하는 비즈니스 문서',      3, 'general_elective', 2, '{AI 활용,문서 작성,보고서}',          '생성형 AI로 공문서·보고서 작성 (융합전공)')
on conflict (code) do update set
  name = excluded.name, credits = excluded.credits, category = excluded.category,
  year_level = excluded.year_level, skills = excluded.skills, description = excluded.description;

-- 2. 강의 자료 (실제 주차 주제 기반, 본문은 시연용으로 새로 쓴 요약)
insert into public.lms_materials (course_code, week_no, title, content) values
  ('SW231', 5, '추상클래스와 인터페이스',
   '추상클래스(abstract class)는 abstract 메서드를 하나 이상 가질 수 있는 클래스로, 직접 객체를 만들 수 없고 상속해서 사용한다. '
   '인터페이스(interface)는 클래스가 구현해야 할 메서드의 목록을 정한 것으로, 한 클래스가 여러 인터페이스를 implements 할 수 있다. '
   '추상클래스는 "is-a" 관계와 공통 코드 재사용에, 인터페이스는 "할 수 있는 기능"을 약속할 때 주로 쓴다.'),
  ('SW231', 8, '예외와 예외처리',
   '예외(Exception)는 프로그램 실행 중 발생하는 오류 상황이다. try 블록에서 예외가 생기면 catch 블록이 처리하고, finally 블록은 예외와 관계없이 항상 실행된다. '
   '일반 예외(checked)는 컴파일러가 처리를 강제하고, 실행 예외(RuntimeException)는 강제하지 않는다. '
   'throws 키워드로 예외 처리를 호출한 메서드에 넘길 수 있고(예외 전가), Exception을 상속해 사용자 정의 예외 클래스를 만들 수 있다.'),
  ('SW231', 11, '쓰레드의 개념과 생성',
   '쓰레드(Thread)는 하나의 프로그램(프로세스) 안에서 동시에 실행되는 작업 흐름이다. '
   '자바에서는 Thread 클래스를 상속해 run()을 재정의하거나, Runnable 인터페이스를 구현해 Thread에 넘기는 방법으로 쓰레드를 만든다. '
   '쓰레드는 start()로 실행해야 새 흐름이 생기며, run()을 직접 부르면 일반 메서드 호출이 된다.'),
  ('SW221', 7, 'AI와 데이터 분석',
   '데이터 분석은 수집 → 정제(전처리) → 탐색 → 분석·모델링 → 시각화·해석 순서로 진행한다. '
   '빅데이터는 양(Volume), 속도(Velocity), 다양성(Variety)이 큰 데이터를 말한다. '
   '파이썬에서는 pandas로 표 데이터를 다루고, matplotlib 등으로 시각화하며, scikit-learn으로 머신러닝 모델을 만든다.'),
  ('SW221', 8, '데이터 수집',
   '데이터는 공공데이터 포털의 파일·API, 웹 페이지, 설문 등에서 수집한다. '
   '파이썬 requests로 웹 API를 호출하면 보통 JSON 응답을 받고, 이를 pandas DataFrame으로 바꿔 분석한다. '
   '웹 페이지를 수집할 때는 사이트 이용약관과 robots.txt를 지키고, 개인정보는 수집하지 않으며, 너무 잦은 요청으로 서버에 부담을 주지 않아야 한다.'),
  ('SW111', 3, 'Git으로 포트폴리오 프로그램 관리하기',
   'Git은 파일의 변경 이력을 기록하는 버전 관리 도구이고, GitHub는 Git 저장소를 온라인에 보관하고 공유하는 서비스다. '
   '기본 흐름은 git init(저장소 만들기) → git add(변경 담기) → git commit(기록 남기기) → git push(GitHub에 올리기)이다. '
   'API 키·비밀번호 같은 비밀값은 .gitignore로 제외해 저장소에 올리지 않아야 한다.'),
  ('GE301', 4, '보고서용 개조식 문장',
   '개조식 문장은 핵심 내용을 짧은 항목으로 나누어 쓰는 방식으로, 보고서와 공문서에서 많이 쓴다. '
   '서술어는 "~함", "~임", "~예정" 같은 명사형으로 끝내고, 한 항목에는 한 가지 내용만 담는다. '
   '중요한 내용부터 쓰고, 숫자·날짜는 구체적으로 적는다.'),
  ('GE301', 10, '부정청탁금지법과 관인의 종류',
   '부정청탁 및 금품등 수수의 금지에 관한 법률(일명 김영란법)은 공직자 등에 대한 부정청탁과 금품 등의 수수를 금지하는 법이다. '
   '관인은 행정기관이 공문서에 찍는 도장으로, 기관 이름으로 찍는 청인과 기관장 등 직위 이름으로 찍는 직인이 있다. '
   '특수관인은 특정 업무(예: 계약, 증명 발급)에만 쓰도록 따로 만든 관인이다.'),
  ('CS204', 10, '통신 프로토콜',
   '프로토콜은 통신하는 장치끼리 데이터를 주고받기 위해 정한 약속(규칙)이다. '
   '프로토콜의 3요소는 구문(데이터 형식), 의미(제어 정보의 뜻), 타이밍(속도와 순서)이다. '
   'OSI 7계층은 물리·데이터링크·네트워크·전송·세션·표현·응용 계층으로 통신 기능을 나누어 정의한다.'),
  ('CS204', 11, '근거리통신망(LAN)',
   '근거리통신망(LAN)은 건물이나 캠퍼스처럼 가까운 범위의 장치를 연결하는 네트워크다. '
   '대표적인 유선 LAN 기술은 이더넷이며, 무선 LAN은 Wi-Fi(IEEE 802.11)이다. '
   '연결 형태(토폴로지)에는 버스형, 링형, 스타형, 메시형이 있고, 오늘날에는 스위치를 중심으로 한 스타형을 주로 쓴다.')
on conflict (course_code, week_no, title) do update set content = excluded.content;

-- 3. 시연 학생 생성 함수: 2026-2 수강 과목과 과제를 실제 시간표 기준으로 교체
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

  -- 이번 학기(2026-2) 과목 중 새 목록에 없는 것은 지운다 (예전 시연 과목 CS311·CS332)
  delete from public.lms_course_history
   where student_id = v_student_id
     and semester = '2026-2'
     and course_code not in ('CS202', 'CS204', 'SW111', 'SW221', 'SW231', 'GE301');

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
      -- 2026-2: 실제 e-class 시간표 6과목 (18학점)
      ('CS202', '2026-2', 'in_progress', null),   -- 데이터베이스기본
      ('CS204', '2026-2', 'in_progress', null),   -- 정보통신개론
      ('SW111', '2026-2', 'in_progress', null),   -- 컴퓨터공학활용
      ('SW221', '2026-2', 'in_progress', null),   -- 파이선프로그래밍실무
      ('SW231', '2026-2', 'in_progress', null),   -- 자바프로그래밍실무
      ('GE301', '2026-2', 'in_progress', null)    -- AI로 완성하는 비즈니스 문서
    ) as v(code, semester, status, grade)
  on conflict (student_id, course_code, semester) do update set
    status = excluded.status, grade = excluded.grade;

  -- 과제: 실제 e-class 과제 이름, 마감은 '지금' 기준 (언제 시연해도 임박 알림이 보이도록)
  insert into public.lms_assignments (course_code, material_id, title, description, due_at)
  select v.course_code,
         (select m.id from public.lms_materials m
           where m.course_code = v.course_code order by m.week_no desc limit 1),
         v.title, v.description, now() + v.due_in
    from (values
      ('CS204', '10주차.정리제출',                   '10주차 통신 프로토콜 수업 내용 정리',               interval '20 hours'),
      ('GE301', '[AI] 김영란법 : 부정청탁방지법',     'AI로 부정청탁금지법 핵심 내용을 개조식으로 정리',    interval '3 days'),
      ('GE301', '[AI] 관인 청인 직인 특수관인 차이',  'AI를 활용해 관인 종류별 차이를 표로 정리',           interval '5 days')
    ) as v(course_code, title, description, due_in)
  on conflict (course_code, title) do update set
    material_id = excluded.material_id, description = excluded.description, due_at = excluded.due_at;

  return v_student_id;
end;
$$;

revoke execute on function public.lms_seed_demo_student(text, text, text) from anon, authenticated, public;
grant  execute on function public.lms_seed_demo_student(text, text, text) to service_role;

-- 4. 이미 만들어진 시연 학생들도 새 시간표로 갱신
select public.lms_seed_demo_student(s.student_no, s.name, s.department)
  from public.lms_students s;
