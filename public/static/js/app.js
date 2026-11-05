/* 화면 보조 동작
 * - 마인드맵 개념(data-ask)을 누르면 교안·챗봇 > 챗봇 탭으로 이동해 질문을 입력한다.
 * - 학습 잔디의 ◀ ▶ 버튼으로 달을 넘긴다.
 */
(function () {
  "use strict";

  function ask(el) {
    const question = el.getAttribute("data-ask");
    const input = document.getElementById("chat-input");
    const group = document.getElementById("tab-study");
    const tab = document.getElementById("tab-chat");
    if (!question || !input || !group || !tab) return;
    group.checked = true;
    tab.checked = true;
    input.value = question.slice(0, 2000);
    input.focus();
    input.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  /* 월별 잔디: ◀ ▶ 로 달 넘기기 (잔디가 HTMX로 다시 그려져도 동작하도록 이벤트 위임) */
  function moveMonth(button) {
    const root = button.closest("#heatmap");
    if (!root) return;
    const months = Array.from(root.querySelectorAll("[data-month]"));
    const current = months.findIndex((m) => !m.hidden);
    const next = Math.min(Math.max(current + Number(button.getAttribute("data-month-nav")), 0), months.length - 1);
    if (next === current) return;
    months.forEach((m, i) => { m.hidden = i !== next; });
    const focusTarget = months[next].querySelector('[data-month-nav="' + button.getAttribute("data-month-nav") + '"]:not([disabled])')
      || months[next].querySelector("[data-month-nav]:not([disabled])");
    if (focusTarget) focusTarget.focus();
  }

  document.addEventListener("click", (event) => {
    const el = event.target instanceof Element ? event.target : null;
    const monthBtn = el ? el.closest("[data-month-nav]") : null;
    if (monthBtn) { moveMonth(monthBtn); return; }
    const target = el ? el.closest("[data-ask]") : null;
    if (target) ask(target);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target instanceof Element ? event.target.closest("[data-ask]") : null;
    if (target) {
      event.preventDefault();
      ask(target);
    }
  });
})();
