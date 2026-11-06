/* LSSA — 예전 웹 푸시용 서비스 워커를 정리하는 파일.
 * 알림은 이제 메일로만 보낸다. 예전에 '알림 켜기'를 눌러 등록된 브라우저가
 * 이 파일을 다시 받으면 스스로 등록을 해제하고, 푸시 구독도 함께 지운다. */
self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    try {
      const sub = await self.registration.pushManager.getSubscription();
      if (sub) await sub.unsubscribe();
    } catch (_) { /* 구독이 없으면 무시 */ }
    await self.registration.unregister();
  })());
});
