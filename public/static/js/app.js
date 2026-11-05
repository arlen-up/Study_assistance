/* 화면 보조 동작: 마인드맵 개념(data-ask)을 누르면 챗봇 탭으로 이동해 질문을 입력한다. */
(function () {
  "use strict";

  function ask(el) {
    const question = el.getAttribute("data-ask");
    const input = document.getElementById("chat-input");
    const tab = document.getElementById("tab-chat");
    if (!question || !input || !tab) return;
    tab.checked = true;
    input.value = question.slice(0, 2000);
    input.focus();
    input.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-ask]") : null;
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
