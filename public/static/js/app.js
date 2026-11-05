/* 화면 보조 동작 (htmx가 서버와 주고받고, 여기서는 작은 편의 기능만)
 * - 챗봇: Enter로 보내기, 보내는 즉시 내 말풍선 + "생각 중" 말풍선, 예시 질문 단추
 * - 답변 속 ```mermaid 그림 그리기 (그림이 있을 때만 mermaid.js를 불러온다, securityLevel=strict)
 * - 교안 뷰어: 열리면 형광펜 부분으로 스크롤
 * - 대시보드 그래프: 마우스·키보드로 값 보기 (data-tip)
 * - 학습 잔디: ◀ ▶ 로 달 넘기기
 */
(function () {
  "use strict";

  const MERMAID_SRC = "/static/vendor/mermaid-12.3.0.min.js";
  const STEPS = ["질문 종류를 알아보는 중…", "교안·학교 시스템에서 찾는 중…", "답변을 쓰는 중…"];

  /* ---------- 화면(메뉴) 바꾸기 ---------- */
  function gotoView(name) {
    const radio = document.getElementById("view-" + name);
    if (radio) radio.checked = true;
  }

  /* ---------- 챗봇: 보내는 즉시 보이는 말풍선 ---------- */
  function chatLog() { return document.getElementById("chat-log"); }

  function scrollChat() {
    const log = chatLog();
    if (log) log.scrollTop = log.scrollHeight;
  }

  function pendingBubble(text, isUser) {
    const row = document.createElement("div");
    row.className = "bubble-row pending " + (isUser ? "user" : "bot");
    const bubble = document.createElement("div");
    bubble.className = "bubble " + (isUser ? "user" : "bot typing");
    if (isUser) {
      bubble.textContent = text;
    } else {
      const dots = document.createElement("span");
      dots.className = "dots";
      dots.setAttribute("aria-hidden", "true");
      dots.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
      const label = document.createElement("span");
      label.className = "typing-label";
      label.textContent = text;
      bubble.append(dots, label);
    }
    row.appendChild(bubble);
    chatLog().appendChild(row);
    scrollChat();
    return bubble;
  }

  let stepTimers = [];
  function startSteps(bubble) {
    const label = bubble.querySelector(".typing-label");
    stepTimers = [1800, 6000].map((ms, i) => setTimeout(() => { if (label) label.textContent = STEPS[i + 1]; }, ms));
  }
  function clearPending() {
    stepTimers.forEach(clearTimeout);
    stepTimers = [];
    document.querySelectorAll("#chat-log .pending").forEach((el) => el.remove());
  }

  document.addEventListener("htmx:beforeRequest", (event) => {
    const elt = event.detail.elt;
    if (!(elt instanceof Element) || !chatLog()) return;
    if (elt.id === "chat-form") {
      const input = document.getElementById("chat-input");
      const text = input ? input.value.trim() : "";
      if (text) pendingBubble(text, true);
      startSteps(pendingBubble(STEPS[0], false));
      if (input) input.value = "";
    } else if (elt.hasAttribute("data-chat-action")) {
      if (elt.hasAttribute("data-goto-view")) gotoView(elt.getAttribute("data-goto-view"));
      pendingBubble(elt.getAttribute("data-chat-action"), false);
    }
  });
  document.addEventListener("htmx:afterRequest", (event) => {
    const elt = event.detail.elt;
    if (elt instanceof Element && (elt.id === "chat-form" || elt.hasAttribute("data-chat-action"))) clearPending();
  });

  /* Enter = 보내기, Shift+Enter = 줄바꿈 (한글 조합 중 Enter는 무시) */
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLTextAreaElement) || target.id !== "chat-input") return;
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (target.value.trim() && target.form) target.form.requestSubmit();
  });

  function askAndSend(question) {
    const input = document.getElementById("chat-input");
    if (!input || !input.form) return;
    gotoView("chat");
    input.value = question.slice(0, 2000);
    input.form.requestSubmit();
  }

  /* ---------- 답변 속 그림 (mermaid) ---------- */
  let mermaidReady = null;
  let diagramSeq = 0;

  function loadMermaid() {
    if (!mermaidReady) {
      mermaidReady = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = MERMAID_SRC;
        script.async = true;
        script.onload = () => {
          window.mermaid.initialize({
            startOnLoad: false,
            securityLevel: "strict",   // 그림 글자 속 HTML·스크립트·클릭 동작 차단
            theme: "neutral",
            fontFamily: getComputedStyle(document.body).fontFamily,
          });
          resolve(window.mermaid);
        };
        script.onerror = () => reject(new Error("mermaid 불러오기 실패"));
        document.head.appendChild(script);
      });
    }
    return mermaidReady;
  }

  async function renderDiagrams(root) {
    const nodes = Array.from((root || document).querySelectorAll("pre.mermaid[data-diagram]:not([data-done])"));
    if (!nodes.length) return;
    nodes.forEach((n) => n.setAttribute("data-done", "1"));
    let mermaid;
    try {
      mermaid = await loadMermaid();
    } catch (e) {
      nodes.forEach((n) => n.closest(".diagram").classList.add("failed"));
      return;
    }
    for (const node of nodes) {
      const figure = node.closest(".diagram");
      const id = "diagram-" + (++diagramSeq);
      try {
        const { svg } = await mermaid.render(id, node.textContent);
        const holder = document.createElement("div");
        holder.className = "diagram-svg";
        holder.innerHTML = svg;   // strict 모드에서 mermaid가 정리(sanitize)한 SVG
        figure.insertBefore(holder, node);
        figure.classList.add("drawn");
      } catch (e) {
        figure.classList.add("failed");
        const stray = document.getElementById("d" + id) || document.getElementById(id);
        if (stray && !figure.contains(stray)) stray.remove();
      }
    }
  }

  /* ---------- 교안 뷰어 ---------- */
  function focusViewer(container) {
    const hl = container.querySelector("[data-viewer-hl]");
    const box = container.querySelector("[data-doc-text]");
    if (hl && box) box.scrollTop = Math.max(0, hl.offsetTop - box.offsetTop - box.clientHeight / 4);
    if (window.matchMedia("(max-width: 1000px)").matches) {
      container.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* ---------- 그래프 값 보기 (마우스·키보드) ---------- */
  let tip = null;
  function showTip(el, x, y) {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      tip.setAttribute("role", "status");
      document.body.appendChild(tip);
    }
    tip.textContent = el.getAttribute("data-tip");
    tip.hidden = false;
    const rect = tip.getBoundingClientRect();
    const left = Math.min(Math.max(8, x - rect.width / 2), window.innerWidth - rect.width - 8);
    const top = y - rect.height - 12 < 8 ? y + 16 : y - rect.height - 12;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  function hideTip() { if (tip) tip.hidden = true; }

  document.addEventListener("pointermove", (event) => {
    const el = event.target instanceof Element ? event.target.closest("[data-tip]") : null;
    if (el) showTip(el, event.clientX, event.clientY); else hideTip();
  });
  document.addEventListener("focusin", (event) => {
    const el = event.target instanceof Element ? event.target.closest("[data-tip]") : null;
    if (!el) { hideTip(); return; }
    const r = el.getBoundingClientRect();
    showTip(el, r.left + r.width / 2, r.top);
  });
  document.addEventListener("scroll", hideTip, true);

  /* ---------- 학습 잔디: ◀ ▶ 로 달 넘기기 (HTMX로 다시 그려져도 동작하도록 이벤트 위임) ---------- */
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

  /* ---------- 클릭 모음 ---------- */
  document.addEventListener("click", (event) => {
    const el = event.target instanceof Element ? event.target : null;
    if (!el) return;
    const monthBtn = el.closest("[data-month-nav]");
    if (monthBtn) { moveMonth(monthBtn); return; }
    const chip = el.closest("[data-ask-send]");
    if (chip) { askAndSend(chip.getAttribute("data-ask-send")); return; }
    const toggle = el.closest("[data-diagram-toggle]");
    if (toggle) {
      const figure = toggle.closest(".diagram");
      const showing = figure.classList.toggle("show-code");
      toggle.textContent = showing ? "그림 보기" : "코드 보기";
      return;
    }
    const goto = el.closest("[data-goto-view]");
    if (goto && !goto.hasAttribute("data-chat-action")) gotoView(goto.getAttribute("data-goto-view"));
  });

  document.addEventListener("htmx:afterSettle", (event) => {
    const target = event.detail.target;
    if (!(target instanceof Element)) return;
    renderDiagrams(target);
    if (target.id === "doc-viewer") focusViewer(target);
  });
  document.addEventListener("DOMContentLoaded", () => renderDiagrams(document));
})();
