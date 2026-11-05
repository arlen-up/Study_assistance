/* "알림 켜기" — Web Push 구독 (서비스 워커 등록 → 권한 요청 → 구독 → 서버 저장)
 * 인증은 HttpOnly 로그인 쿠키로 하며, 같은 사이트 요청만 보낸다.
 *
 * 맥·윈도우 호환
 * - 운영체제(맥/윈도우/아이폰…)와 브라우저를 알아내서, 안 될 때 고칠 곳을 그 기기에 맞게 알려 준다.
 * - "이 기기에서 바로 확인": 서버를 거치지 않고 이 기기에서 바로 알림을 띄워 본다.
 *   → 이것도 안 뜨면 컴퓨터(운영체제) 알림 설정 문제, 이건 되는데 서버 알림이 안 오면 서버·구독 문제.
 * - 사파리는 권한 요청이 반드시 '버튼 클릭' 안에서 일어나야 해서, 클릭하자마자 권한부터 묻는다.
 * - 한 브라우저에서 여러 계정으로 로그인하면, 열 때마다 구독을 지금 계정으로 다시 묶는다.
 */
(function () {
  "use strict";

  const ICON = "/static/img/icon-192.png";
  const BADGE = "/static/img/badge-96.png";

  function urlBase64ToUint8Array(base64) {
    const padding = "=".repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  function sameKey(buffer, expected) {
    if (!buffer) return true; // 일부 브라우저는 키를 알려 주지 않음 → 같다고 본다
    const a = new Uint8Array(buffer);
    return a.length === expected.length && a.every((v, i) => v === expected[i]);
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

  /* 운영체제·브라우저 알아내기 (userAgentData가 있으면 우선, 없으면 userAgent 글자로) */
  function detectDevice() {
    const ua = navigator.userAgent || "";
    const uaData = navigator.userAgentData;
    const platform = ((uaData && uaData.platform) || navigator.platform || "").toLowerCase();
    const touchMac = platform.indexOf("mac") >= 0 && navigator.maxTouchPoints > 1; // 아이패드(데스크톱 모드)
    let os = "other";
    if (/iphone|ipad|ipod/i.test(ua) || touchMac) os = "ios";
    else if (/android/i.test(ua)) os = "android";
    else if (platform.indexOf("mac") >= 0 || /Macintosh/.test(ua)) os = "mac";
    else if (platform.indexOf("win") >= 0 || /Windows/.test(ua)) os = "windows";

    let browser = "브라우저";
    if (/Edg\//.test(ua)) browser = "Edge";
    else if (/Whale\//.test(ua)) browser = "Whale";
    else if (/Firefox\/|FxiOS/.test(ua)) browser = "Firefox";
    else if (/Chrome\/|CriOS/.test(ua)) browser = "Chrome";
    else if (/Safari\//.test(ua)) browser = "Safari";

    const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
    return { os, browser, standalone };
  }

  /* 옛 사파리는 콜백 방식, 요즘 브라우저는 Promise 방식 → 둘 다 받기 */
  function askPermission() {
    return new Promise((resolve) => {
      const result = Notification.requestPermission(resolve);
      if (result && typeof result.then === "function") result.then(resolve);
    });
  }

  function unsupportedMessage(device) {
    if (!window.isSecureContext) return "알림은 https 주소(또는 내 컴퓨터 localhost)에서만 켤 수 있어요.";
    if (device.os === "ios" && !device.standalone) {
      return "아이폰·아이패드는 사파리에서 공유 버튼 → '홈 화면에 추가'로 설치한 뒤, 그 앱에서 알림을 켤 수 있어요. (iOS 16.4 이상)";
    }
    if (device.os === "mac" && device.browser === "Safari") {
      return "맥 사파리는 macOS 13(벤추라) 이상에서 알림을 받을 수 있어요. 크롬이나 엣지를 써도 돼요.";
    }
    return "이 브라우저는 푸시 알림을 지원하지 않아요. 크롬·엣지·사파리 최신 버전에서 열어 주세요.";
  }

  function deniedMessage(device) {
    if (device.browser === "Safari") {
      return "알림이 차단되어 있어요. Safari → 설정 → 웹사이트 → 알림에서 이 사이트를 '허용'으로 바꿔 주세요.";
    }
    return "알림이 차단되어 있어요. 주소창 왼쪽 🔒(사이트 정보) → 알림 → '허용'으로 바꾼 뒤 새로고침해 주세요.";
  }

  /* '알림이 안 와요?' 안내: 지금 기기에 맞는 설명만 보여 준다 (JS가 없으면 모두 보임) */
  function tailorHelp(card, device) {
    const help = card.querySelector("[data-push-help]");
    if (!help) return;
    const known = ["mac", "windows", "ios", "android"].indexOf(device.os) >= 0 ? device.os : null;
    help.querySelectorAll("[data-os]").forEach((block) => {
      block.hidden = known !== null && block.getAttribute("data-os") !== known;
    });
    if (device.browser !== "브라우저") {
      help.querySelectorAll("[data-browser]").forEach((el) => { el.textContent = device.browser; });
    }
  }

  function init() {
    const card = document.getElementById("push-card");
    if (!card) return;
    const status = card.querySelector("[data-push-status]");
    const enableBtn = card.querySelector("[data-push-enable]");
    const disableBtn = card.querySelector("[data-push-disable]");
    const testBtn = card.querySelector("[data-push-test]");
    const localBtn = card.querySelector("[data-push-local]");
    const publicKey = card.dataset.vapidKey;
    const device = detectDevice();
    card.dataset.os = device.os;
    tailorHelp(card, device);

    const show = (text, tone) => {
      status.textContent = text;
      status.className = "small " + (tone === "error" ? "text-danger" : "muted");
    };
    const setOn = (on) => {
      enableBtn.hidden = on;
      disableBtn.hidden = !on;
      testBtn.hidden = !on;
    };

    const supported = window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
    if (!supported) {
      show(unsupportedMessage(device));
      enableBtn.disabled = true;
      if (localBtn) localBtn.hidden = true;
      return;
    }
    if (localBtn) localBtn.hidden = false;
    if (!publicKey) { show("서버에 푸시 알림 키가 설정되지 않았어요. '이 기기에서 바로 확인'은 쓸 수 있어요."); enableBtn.disabled = true; }

    navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(async (reg) => {
      if (!publicKey) return;
      const existing = await reg.pushManager.getSubscription();
      if (existing && Notification.permission === "granted") {
        setOn(true);
        show("알림이 켜져 있어요. 매일 저녁 8시쯤 알려드려요.");
        // 같은 브라우저로 다른 계정에 로그인했을 수 있으니, 이 기기를 지금 계정에 다시 묶는다 (조용히)
        postJson("/api/push/subscribe", existing.toJSON()).catch(() => {});
      } else {
        setOn(false);
        show(Notification.permission === "denied" ? deniedMessage(device) : "알림을 켜면 과제 마감·복습 시간을 알려드려요.",
          Notification.permission === "denied" ? "error" : "ok");
      }
    }).catch(() => show("서비스 워커를 등록하지 못했어요. 시크릿 창이면 일반 창에서 열어 주세요.", "error"));

    enableBtn.addEventListener("click", async () => {
      enableBtn.disabled = true;
      try {
        // 사파리: 클릭 직후 바로 권한을 물어야 창이 뜬다 (await 전에 다른 작업을 하지 않는다)
        const permission = Notification.permission === "granted" ? "granted" : await askPermission();
        if (permission !== "granted") throw new Error(permission === "denied" ? deniedMessage(device) : "알림 권한이 허용되지 않았어요.");
        const reg = await navigator.serviceWorker.ready;
        const key = urlBase64ToUint8Array(publicKey);
        let sub = await reg.pushManager.getSubscription();
        if (sub && !sameKey(sub.options && sub.options.applicationServerKey, key)) {
          await sub.unsubscribe(); // 서버 키가 바뀌었으면 예전 구독으로는 받을 수 없다
          sub = null;
        }
        sub = sub || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }));
        const result = await postJson("/api/push/subscribe", sub.toJSON());
        setOn(true);
        show(result.message + " 매일 저녁 8시쯤 알려드려요.");
      } catch (e) {
        show(e.message || "알림을 켜지 못했어요.", "error");
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
        show(result.ok ? result.message + " 몇 초 안에 안 오면 아래 '알림이 안 와요?'를 확인해 주세요." : result.message,
          result.ok ? "ok" : "error");
      } catch (e) {
        show(e.message, "error");
      } finally {
        testBtn.disabled = false;
      }
    });

    /* 서버 없이 이 기기에서 바로 알림 띄우기 → 운영체제 설정 문제인지 바로 알 수 있다 */
    if (localBtn) {
      localBtn.addEventListener("click", async () => {
        localBtn.disabled = true;
        try {
          const permission = Notification.permission === "granted" ? "granted" : await askPermission();
          if (permission !== "granted") throw new Error(deniedMessage(device));
          const reg = await navigator.serviceWorker.ready;
          await reg.showNotification("🔔 이 기기 알림 확인", {
            body: "이 알림이 보이면 컴퓨터 알림 설정은 괜찮아요!",
            icon: ICON,
            badge: BADGE,
            tag: "local-test",
            renotify: true,
            lang: "ko",
            data: { url: "/" },
          });
          show("방금 화면 구석에 알림이 떴나요? 안 보이면 아래 '알림이 안 와요?'에서 " +
            (device.os === "mac" ? "맥" : device.os === "windows" ? "윈도우" : "기기") + " 설정을 확인해 주세요.");
        } catch (e) {
          show(e.message || "알림을 띄우지 못했어요.", "error");
        } finally {
          localBtn.disabled = false;
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
