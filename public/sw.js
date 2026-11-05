/* LMS 3분 학습 도우미 — 서비스 워커 (Web Push 수신)
 * 브라우저가 닫혀 있어도 푸시를 받아 알림을 띄운다.
 * 보안: 알림 클릭 시 이동 주소는 같은 사이트의 경로('/...')만 허용한다 (오픈 리다이렉트 방지).
 */

const DEFAULT_TITLE = "LMS 3분 학습 도우미";
const ICON = "/static/icon.svg";

function safePath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : "/";
}

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = typeof data.title === "string" && data.title ? data.title : DEFAULT_TITLE;
  event.waitUntil(
    self.registration.showNotification(title, {
      body: typeof data.body === "string" ? data.body : "",
      icon: ICON,
      badge: ICON,
      tag: typeof data.tag === "string" ? data.tag : "lms-reminder",
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
          client.focus();
          return client.navigate ? client.navigate(target.href) : undefined;
        }
      }
      return self.clients.openWindow(target.href);
    })
  );
});
