/* "알림 켜기" — Web Push 구독 (서비스 워커 등록 → 권한 요청 → 구독 → 서버 저장)
 * 인증은 HttpOnly 로그인 쿠키로 하며, 같은 사이트 요청만 보낸다.
 */
(function () {
  "use strict";

  function urlBase64ToUint8Array(base64) {
    const padding = "=".repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* 빈 응답 */ }
    if (!res.ok) {
      const message = (data.error && data.error.message) || "요청에 실패했어요 (HTTP " + res.status + ")";
      throw new Error(message);
    }
    return data;
  }

  function init() {
    const card = document.getElementById("push-card");
    if (!card) return;
    const status = card.querySelector("[data-push-status]");
    const enableBtn = card.querySelector("[data-push-enable]");
    const disableBtn = card.querySelector("[data-push-disable]");
    const testBtn = card.querySelector("[data-push-test]");
    const publicKey = card.dataset.vapidKey;

    const show = (text, tone) => {
      status.textContent = text;
      status.className = "small " + (tone === "error" ? "text-danger" : "muted");
    };
    const setOn = (on) => {
      enableBtn.hidden = on;
      disableBtn.hidden = !on;
      testBtn.hidden = !on;
    };

    if (!publicKey) { show("서버에 푸시 알림 키가 설정되지 않았어요."); enableBtn.disabled = true; return; }
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      show("이 브라우저는 푸시 알림을 지원하지 않아요. (아이폰은 '홈 화면에 추가' 후 사용할 수 있어요)");
      enableBtn.disabled = true;
      return;
    }

    navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(async (reg) => {
      const existing = await reg.pushManager.getSubscription();
      if (existing && Notification.permission === "granted") {
        setOn(true);
        show("알림이 켜져 있어요. 매일 저녁 8시쯤 알려드려요.");
      } else {
        setOn(false);
        show(Notification.permission === "denied"
          ? "브라우저 설정에서 알림이 차단되어 있어요. 사이트 설정에서 허용해 주세요."
          : "알림을 켜면 과제 마감·복습 시간을 알려드려요.");
      }
    }).catch(() => show("서비스 워커를 등록하지 못했어요.", "error"));

    enableBtn.addEventListener("click", async () => {
      enableBtn.disabled = true;
      try {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") throw new Error("알림 권한이 허용되지 않았어요.");
        const reg = await navigator.serviceWorker.ready;
        const sub = (await reg.pushManager.getSubscription()) ||
          (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(publicKey) }));
        const result = await postJson("/api/push/subscribe", sub.toJSON());
        setOn(true);
        show(result.message + " 매일 저녁 8시쯤 알려드려요.");
      } catch (e) {
        show(e.message, "error");
      } finally {
        enableBtn.disabled = false;
      }
    });

    disableBtn.addEventListener("click", async () => {
      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await postJson("/api/push/unsubscribe", { endpoint: sub.endpoint });
          await sub.unsubscribe();
        }
        setOn(false);
        show("알림을 껐어요.");
      } catch (e) {
        show(e.message, "error");
      }
    });

    testBtn.addEventListener("click", async () => {
      testBtn.disabled = true;
      try {
        const result = await postJson("/api/push/test");
        show(result.message, result.ok ? "ok" : "error");
      } catch (e) {
        show(e.message, "error");
      } finally {
        testBtn.disabled = false;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
