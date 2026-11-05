"""AI 답변(마크다운)을 화면용 HTML로 안전하게 바꾼다.

🔒 보안 (AI 답변에는 교안 속 악성 글이 섞여 들어올 수 있다고 보고 처리)
- 마크다운 안의 HTML 태그는 쓰지 못하게 한다 → 글자로 보이게 이스케이프 (XSS 방지)
- 링크는 http(s)·mailto·#만 허용하고, 새 창 + noopener 로 연다
- 이미지 문법은 끈다 (외부 추적 이미지 방지)
- ```mermaid 블록은 <pre class="mermaid">에 글자 그대로(이스케이프) 담는다.
  실제 그림은 브라우저의 mermaid.js가 securityLevel="strict"로 그린다.

[1], [2] 인용 번호는 같은 답변의 출처 목록으로 이어지는 작은 링크로 바꾼다.

한국어 굵은 글씨: CommonMark 규칙에서는 `**예외(Exception)**는`처럼 닫는 ** 앞이 문장부호이고
뒤에 조사가 바로 붙으면 굵게 처리되지 않는다. 그래서 파싱 전에 **…** 짝을 표시 문자로 바꿔 두었다가
HTML로 만든 뒤 <strong>으로 되돌린다 (코드 안의 **는 그대로).
"""

import re
from html import escape
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from markupsafe import Markup

_ALLOWED_LINK = re.compile(r"^(https?://|mailto:|#)", re.IGNORECASE)
_LANG = re.compile(r"^[a-z0-9+#._-]{1,20}$")
_CITE = re.compile(r"\[(\d{1,2})\]")
_CODE_SPANS = re.compile(r"(```[\s\S]*?(?:```|$)|`[^`\n]+`)")
_BOLD = re.compile(r"\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*")
_BOLD_OPEN, _BOLD_CLOSE = "\ue000", "\ue001"   # 유니코드 개인 영역 문자 (답변에 나올 일이 없는 글자)


def _protect_bold(text: str) -> str:
    text = text.replace(_BOLD_OPEN, "").replace(_BOLD_CLOSE, "")
    parts = _CODE_SPANS.split(text)
    return "".join(
        part if i % 2 else _BOLD.sub(lambda m: f"{_BOLD_OPEN}{m.group(1)}{_BOLD_CLOSE}", part)
        for i, part in enumerate(parts)
    )


def _validate_link(url: str) -> bool:
    return bool(_ALLOWED_LINK.match(url.strip()))


def _fence(self: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType) -> str:
    token = tokens[idx]
    lang = token.info.strip().split()[0].lower() if token.info.strip() else ""
    code = escape(token.content)
    if lang == "mermaid":
        return (
            '<figure class="diagram">'
            f'<pre class="mermaid" data-diagram>{code}</pre>'
            '<figcaption class="diagram-caption">그림으로 그린 내용이에요 · <button type="button" class="link-btn" '
            'data-diagram-toggle>코드 보기</button></figcaption>'
            "</figure>\n"
        )
    if lang and _LANG.match(lang):
        return f'<pre class="code"><span class="code-lang">{escape(lang)}</span><code class="language-{escape(lang)}">{code}</code></pre>\n'
    return f'<pre class="code"><code>{code}</code></pre>\n'


def _code_block(self: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType) -> str:
    return f'<pre class="code"><code>{escape(tokens[idx].content)}</code></pre>\n'


def _link_open(self: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType) -> str:
    token = tokens[idx]
    token.attrSet("target", "_blank")
    token.attrSet("rel", "noopener noreferrer nofollow")
    return self.renderToken(tokens, idx, options, env)


def _text(self: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType) -> str:
    content = escape(tokens[idx].content, quote=False)
    refs: set[int] = env.get("refs") or set()
    prefix: str = env.get("cite_prefix") or ""
    if not refs or not prefix:
        return content

    def repl(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if number not in refs:
            return match.group(0)
        return f'<a class="cite" href="#{prefix}-{number}" aria-label="출처 {number}번 보기">{number}</a>'

    return _CITE.sub(repl, content)


def _table_open(self: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType) -> str:
    return '<div class="table-wrap"><table>'


def _table_close(self: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType) -> str:
    return "</table></div>"


def _build() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    md.disable(["image"])
    md.validateLink = _validate_link  # type: ignore[method-assign]
    md.add_render_rule("fence", _fence)
    md.add_render_rule("code_block", _code_block)
    md.add_render_rule("link_open", _link_open)
    md.add_render_rule("text", _text)
    md.add_render_rule("table_open", _table_open)
    md.add_render_rule("table_close", _table_close)
    return md


_MD = _build()


def render_markdown(text: str | None, refs: Any = None, cite_prefix: str = "") -> Markup:
    """마크다운 → 안전한 HTML. refs(인용 번호 목록)와 cite_prefix를 주면 [n]을 출처 링크로 바꾼다."""
    env: dict[str, Any] = {"refs": {int(r) for r in refs or []}, "cite_prefix": cite_prefix}
    rendered = _MD.render(_protect_bold(text or ""), env)
    rendered = rendered.replace(_BOLD_OPEN, "<strong>").replace(_BOLD_CLOSE, "</strong>")
    return Markup(rendered)  # noqa: S704 — 위 규칙으로 만든 HTML만 들어 있음
