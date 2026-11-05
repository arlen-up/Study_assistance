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
import re
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
# 맥 사파리(Apple)·윈도우 엣지(WNS)·크롬(FCM) 모두 표준 Web Push 헤더를 따른다.
# Urgency가 없으면 일부 서비스가 절전 중인 기기에 늦게 전달한다.
PUSH_HEADERS = {"Urgency": "normal"}
_SUBJECT = re.compile(r"^(mailto:[^@\s]+@[^@\s]+\.[^@\s]+|https://[^\s/]+\S*)$")


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


def vapid_subject(settings: Settings) -> str:
    """VAPID 'sub'(연락처). Apple(맥 사파리)은 형식이 틀리면 403 BadJwtToken으로 거절한다.

    'mailto: a@b.com'처럼 공백이 섞인 값은 고쳐 주고, 그래도 틀리면 설정 오류로 알린다.
    """
    subject = re.sub(r"^mailto:\s+", "mailto:", settings.vapid_subject.strip())
    if not _SUBJECT.fullmatch(subject):
        raise PushNotConfiguredError("VAPID_SUBJECT는 'mailto:주소' 또는 'https://주소' 형식이어야 합니다.")
    return subject


def subject_warning(settings: Settings) -> str | None:
    """사파리 발송이 거절될 수 있는 연락처면 안내 문구 (예시 주소·localhost)."""
    try:
        subject = vapid_subject(settings)
    except PushNotConfiguredError as exc:
        return exc.message
    host = subject.split("@", 1)[-1] if subject.startswith("mailto:") else urlparse(subject).hostname or ""
    if host in {"example.com", "localhost"} or host.endswith((".example.com", ".local")):
        return "VAPID_SUBJECT가 예시 주소예요. 맥 사파리 알림이 거절될 수 있으니 실제 연락처로 바꿔 주세요."
    return None


_subject_warned = False


def _warn_subject_once(settings: Settings) -> None:
    global _subject_warned
    if not _subject_warned:
        _subject_warned = True
        if warning := subject_warning(settings):
            logger.warning(warning)


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
        vapid_claims={"sub": vapid_subject(settings), "aud": f"{parsed.scheme}://{parsed.netloc}"},
        # aes128gcm(RFC 8291)은 Apple·WNS·FCM이 모두 받는 유일한 공통 암호화 방식 (사파리는 이것만 받음)
        content_encoding="aes128gcm",
        ttl=PUSH_TTL_SECONDS,
        headers=dict(PUSH_HEADERS),
        timeout=PUSH_TIMEOUT_SECONDS,
    )


async def send_to_user(db: AsyncClient, settings: Settings, user_id: UUID, payload: PushPayload) -> int:
    """사용자의 모든 구독(기기)으로 보낸다. 성공한 기기 수를 돌려준다."""
    _require_push(settings)
    vapid_subject(settings)  # 형식이 틀리면 기기마다 실패하기 전에 한 번에 알린다
    _warn_subject_once(settings)
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
