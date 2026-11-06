-- =====================================================================
-- 알림을 메일로만 (웹 푸시·카카오톡 제외)
--   users.email_alerts : 메일 알림 받기 (학생이 직접 켜야 보낸다 — 기본 꺼짐)
--   users.alert_email  : 받을 주소 (비우면 가입한 메일)
--   매일 저녁 Cron이 받기를 켠 학생에게만, 하루 한 번 가장 급한 알림 하나를 보낸다 (notification_logs로 중복 방지).
--   예전 웹 푸시 구독(push_subscriptions)은 더 이상 쓰지 않는다 (기록만 남겨 둠).
--   다시 실행해도 안전하다.
-- =====================================================================
alter table public.users add column if not exists email_alerts boolean not null default false;
alter table public.users add column if not exists alert_email text;

alter table public.users drop constraint if exists users_alert_email_format;
alter table public.users add constraint users_alert_email_format check (
  alert_email is null
  or (length(alert_email) <= 254 and alert_email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
);

-- 테스트 메일도 기록해 하루 보낼 수 있는 횟수를 제한한다 (Gmail 발송 한도·남용 방지)
alter table public.notification_logs drop constraint if exists notification_logs_kind_check;
alter table public.notification_logs add constraint notification_logs_kind_check
  check (kind in ('review_due', 'assignment_due', 'streak_risk', 'email_test'));
comment on column public.notification_logs.delivered is '성공한 발송 수 (메일 1통 = 1)';
