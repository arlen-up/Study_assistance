/* LSSA (Study Overflow) — 서비스 워커 (Web Push 수신)
 * 브라우저가 닫혀 있어도 푸시를 받아 알림을 띄운다.
 * 보안: 알림 클릭 시 이동 주소는 같은 사이트의 경로('/...')만 허용한다 (오픈 리다이렉트 방지).
 *
 * 맥·윈도우 호환
 * - 아이콘은 PNG만 쓴다. 윈도우·맥의 크롬/엣지는 SVG 아이콘을 알림에 그리지 못해 빈 칸이 된다.
 * - 같은 tag로 다시 오면 조용히 바뀌기만 하므로 renotify로 다시 알려 준다 (사파리는 무시).
 * - 사파리(맥)는 푸시를 받으면 반드시 알림을 띄워야 한다 → 내용이 이상해도 기본 문구로 띄운다.
 */

const DEFAULT_TITLE = "LSSA";
const ICON = "/static/img/icon-192.png";
const BADGE = "/static/img/badge-96.png";   // 안드로이드 상태 표시줄용 (흰색 실루엣)

function safePath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : "/";
}

function readPayload(event) {
  if (!event.data) return {};
  try {
    const data = event.data.json();
    return data && typeof data === "object" ? data : {};
  } catch (e) {
    return { body: event.data.text() };
  }
}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  const data = readPayload(event);
  const title = typeof data.title === "string" && data.title ? data.title : DEFAULT_TITLE;
  const tag = typeof data.tag === "string" && data.tag ? data.tag : "lms-reminder";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: typeof data.body === "string" ? data.body : "",
      icon: ICON,
      badge: BADGE,
      tag,
      renotify: true,
      lang: "ko",
      data: { url: safePath(data.url) },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(safePath(event.notification.data && event.notification.data.url), self.location.origin);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (new URL(client.url).origin === self.location.origin && "focus" in client) {
          // 사파리는 navigate가 없거나 실패할 수 있어, 실패하면 새 창으로 연다
          return client.focus().then((c) => (c && c.navigate ? c.navigate(target.href) : null))
            .catch(() => self.clients.openWindow(target.href));
        }
      }
      return self.clients.openWindow(target.href);
    })
  );
});
