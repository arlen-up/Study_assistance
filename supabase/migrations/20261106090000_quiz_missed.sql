-- =====================================================================
-- 적응형 학습: 퀴즈에서 틀린 문제 기록
--   study_logs.missed = [{"question": "...", "answer": "정답", "explanation": "해설"}, ...]
--   같은 교안으로 다음 퀴즈를 만들 때 틀린 개념을 다른 방식으로 다시 묻는다 (agent_service.generate_quiz).
--   복습 간격은 퀴즈 점수로 정한다 (review_service — 잘 풀면 1·3·7일, 3문제 중 1개 이하면 1일 뒤 다시).
--   다시 실행해도 안전하다.
-- =====================================================================
alter table public.study_logs add column if not exists missed jsonb;
