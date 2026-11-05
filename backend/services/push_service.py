"""브라우저 Web Push 구독 관리와 발송.

🔒 보안
- 구독 endpoint는 브라우저가 보내는 '주소'이고 서버가 그 주소로 요청을 보낸다.
  임의 주소를 허용하면 서버를 이용해 내부망을 찌르는 SSRF가 가능하므로,
  알려진 푸시 서비스 호스트(FCM·Mozilla·Apple·Windows)만 허용한다.
- 구독 키(p256dh/auth)는 RLS 정책 없는 테이블에 저장되어 서버(secret 키)만 읽는다.
- 만료(404/410)된 구독은 바로 지우고, 계속 실패하는 구독은 정리한다.
"""

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from pywebpush import WebPushException, webpush
from supabase import AsyncClient

from backend.core.config import Settings
from backend.core.errors import AppError, BadRequestError, UpstreamError
from backend.schemas.models import PushSubscriptionIn

logger = logging.getLogger(__name__)

PUSH_TTL_SECONDS = 6 * 3600
PUSH_TIMEOUT_SECONDS = 10
MAX_FAILURES = 5


class PushNotConfiguredError(AppError):
    status_code = 503
    code = "push_not_configured"


@dataclass(frozen=True)
class PushPayload:
    title: str
    body: str
    url: str = "/"
    tag: str = "lms-reminder"

    def to_json(self) -> str:
        # 알림 클릭 시 이동할 주소는 같은 사이트의 경로만 허용 (서비스 워커에서도 다시 검사)
        url = self.url if self.url.startswith("/") and not self.url.startswith("//") else "/"
        return json.dumps({"title": self.title, "body": self.body, "url": url, "tag": self.tag}, ensure_ascii=False)


def is_allowed_endpoint(endpoint: str, settings: Settings) -> bool:
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in settings.push_hosts)


def _require_push(settings: Settings) -> None:
    if not settings.push_enabled:
        raise PushNotConfiguredError("푸시 알림 키(VAPID)가 설정되지 않았습니다.")


async def save_subscription(
    db: AsyncClient, settings: Settings, user_id: UUID, sub: PushSubscriptionIn, user_agent: str | None
) -> None:
    _require_push(settings)
    if not is_allowed_endpoint(sub.endpoint, settings):
        raise BadRequestError("지원하지 않는 푸시 서비스 주소입니다.", code="push_endpoint_not_allowed")
    try:
        await db.table("push_subscriptions").upsert(
            {
                "user_id": str(user_id),
                "endpoint": sub.endpoint,
                "p256dh": sub.keys.p256dh,
                "auth": sub.keys.auth,
                "user_agent": (user_agent or "")[:300],
                "failure_count": 0,
            },
            on_conflict="endpoint",
        ).execute()
    except Exception:
        logger.exception("푸시 구독 저장 실패")
        raise UpstreamError("알림 구독을 저장하지 못했습니다.") from None


async def delete_subscription(db: AsyncClient, user_id: UUID, endpoint: str) -> None:
    try:
        await db.table("push_subscriptions").delete().eq("user_id", str(user_id)).eq("endpoint", endpoint).execute()
    except Exception:
        logger.exception("푸시 구독 삭제 실패")
        raise UpstreamError("알림 구독을 해제하지 못했습니다.") from None


async def list_subscriptions(db: AsyncClient, user_id: UUID) -> list[dict[str, Any]]:
    res = (
        await db.table("push_subscriptions")
        .select("id,endpoint,p256dh,auth,failure_count")
        .eq("user_id", str(user_id))
        .execute()
    )
    return list(res.data or [])


async def subscribed_user_ids(db: AsyncClient) -> set[UUID]:
    res = await db.table("push_subscriptions").select("user_id").execute()
    return {UUID(r["user_id"]) for r in res.data or []}


def _send_one(settings: Settings, sub: dict[str, Any], payload: str) -> None:
    """pywebpush는 동기 함수라 스레드에서 실행한다."""
    endpoint = sub["endpoint"]
    parsed = urlparse(endpoint)
    if settings.vapid_private_key is None:
        raise PushNotConfiguredError("푸시 알림 키(VAPID)가 설정되지 않았습니다.")
    webpush(
        subscription_info={"endpoint": endpoint, "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
        data=payload,
        vapid_private_key=settings.vapid_private_key.get_secret_value(),
        # pywebpush가 claims를 수정하므로 매번 새 dict를 넘긴다
        vapid_claims={"sub": settings.vapid_subject, "aud": f"{parsed.scheme}://{parsed.netloc}"},
        ttl=PUSH_TTL_SECONDS,
        timeout=PUSH_TIMEOUT_SECONDS,
    )


async def send_to_user(db: AsyncClient, settings: Settings, user_id: UUID, payload: PushPayload) -> int:
    """사용자의 모든 구독(기기)으로 보낸다. 성공한 기기 수를 돌려준다."""
    _require_push(settings)
    delivered = 0
    body = payload.to_json()
    for sub in await list_subscriptions(db, user_id):
        if not is_allowed_endpoint(sub["endpoint"], settings):
            await db.table("push_subscriptions").delete().eq("id", sub["id"]).execute()
            continue
        try:
            await asyncio.to_thread(_send_one, settings, sub, body)
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):  # 브라우저가 구독을 해지함
                await db.table("push_subscriptions").delete().eq("id", sub["id"]).execute()
                continue
            failures = int(sub.get("failure_count") or 0) + 1
            logger.warning("푸시 발송 실패 (status=%s, 연속 %d회)", status, failures)
            if failures >= MAX_FAILURES:
                await db.table("push_subscriptions").delete().eq("id", sub["id"]).execute()
            else:
                await db.table("push_subscriptions").update({"failure_count": failures}).eq("id", sub["id"]).execute()
            continue
        except Exception:
            logger.exception("푸시 발송 중 예기치 못한 오류")
            continue
        delivered += 1
        await db.table("push_subscriptions").update(
            {"failure_count": 0, "last_success_at": dt.datetime.now(dt.UTC).isoformat()}
        ).eq("id", sub["id"]).execute()
    return delivered
