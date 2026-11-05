#!/usr/bin/env bash
# .env.local 의 값을 Vercel production 환경변수로 등록한다.
# - 값은 명령줄 인자가 아닌 표준입력(stdin)으로만 전달 (프로세스 목록에 노출되지 않음)
# - 화면에는 변수 이름과 성공/실패만 출력 (값은 출력하지 않음)
# - 비밀값은 Sensitive(등록 후 다시 볼 수 없음)로 저장
# 사용법: bash scripts/vercel_env_push.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/opt/node@24/bin:$PATH"

if [ ! -f .env.local ]; then
  echo "❌ .env.local 이 없습니다." >&2
  exit 1
fi

CONFIG="SUPABASE_URL SUPABASE_PUBLISHABLE_KEY LLM_BASE_URL LLM_CHAT_MODEL LLM_REASONING_EFFORT LLM_EMBEDDING_MODEL EMBEDDING_DIMENSIONS EMBEDDING_SEND_DIMENSIONS VAPID_PUBLIC_KEY VAPID_SUBJECT"
SENSITIVE="SUPABASE_SECRET_KEY LLM_API_KEY LMS_API_KEY VAPID_PRIVATE_KEY CRON_SECRET"

read_value() {
  # NAME=값   # 주석  → 값만 꺼낸다 (= 가 들어간 base64 값도 유지)
  grep -E "^$1=" .env.local | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//'
}

push() {
  local name="$1" flag="$2" value
  value="$(read_value "$name")"
  if [ -z "$value" ]; then
    echo "⏭  $name (값 없음, 건너뜀)"
    return
  fi
  if printf '%s' "$value" | npx -y vercel@65.11.0 env add "$name" production "$flag" --force >/dev/null 2>&1; then
    echo "✅ $name"
  else
    echo "❌ $name (실패 — 로그인/프로젝트 연결을 확인하세요)"
  fi
}

echo "Vercel production 환경변수 등록 (값은 출력하지 않습니다)"
for name in $CONFIG; do push "$name" --no-sensitive; done
for name in $SENSITIVE; do push "$name" --sensitive; done
echo "완료. 확인: npx vercel@65.11.0 env ls production"
