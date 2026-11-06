"""교안 뷰어 — 챗봇 옆 빈 공간에 출처 교안을 띄운다.

- 원본 PDF가 보관돼 있으면: PDF를 그대로 띄우고 출처 쪽(page)으로 바로 이동
- 원본이 없으면(코스모스에서 가져온 글, 텍스트 교안 등): 본문 글자를 보여 주고 답변에 쓴 부분을 형광펜으로 표시
"""

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from supabase import AsyncClient

from backend.core.errors import NotFoundError, UpstreamError
from backend.services import file_store

logger = logging.getLogger(__name__)

MAX_VIEW_CHARS = 30_000   # 화면에 한 번에 보여 줄 본문 길이 (형광펜 부분을 가운데에 둔다)
MAX_OVERLAP = 300         # 청크끼리 겹치는 글자 (document_service.CHUNK_OVERLAP=120보다 넉넉히)


@dataclass(frozen=True)
class ViewerBlock:
    page: int | None
    before: str
    highlight: str = ""
    after: str = ""
    trimmed_start: bool = False
    trimmed_end: bool = False


@dataclass
class ViewerDoc:
    id: UUID
    title: str
    course_name: str | None
    week_no: int | None
    source_type: str
    page: int | None = None
    has_pdf: bool = False
    highlight_text: str = ""  # PDF 쪽에서 강조할 근거 문장
    blocks: list[ViewerBlock] = field(default_factory=list)
    highlighted: bool = False


def _merge_overlap(prev: str, nxt: str) -> str:
    """앞 청크의 끝과 뒤 청크의 시작이 겹치면 한 번만 남기고 잇는다."""
    for k in range(min(len(prev), len(nxt), MAX_OVERLAP), 15, -1):
        if prev.endswith(nxt[:k]):
            return prev + nxt[k:]
    return f"{prev}\n{nxt}"


def _find(text: str, needle: str) -> tuple[int, int]:
    needle = needle.strip()
    if not needle:
        return -1, 0
    start = text.find(needle)
    if start >= 0:
        return start, len(needle)
    head = needle[:80].strip()
    start = text.find(head) if len(head) >= 20 else -1
    return (start, min(len(needle), len(text) - start)) if start >= 0 else (-1, 0)


def _block(page: int | None, text: str, needle: str | None) -> ViewerBlock:
    start, length = _find(text, needle or "")
    if start < 0:
        cut = text[:MAX_VIEW_CHARS]
        return ViewerBlock(page, cut, trimmed_end=len(text) > len(cut))
    lo = max(0, start - MAX_VIEW_CHARS // 2)
    hi = min(len(text), start + length + MAX_VIEW_CHARS // 2)
    return ViewerBlock(
        page,
        before=text[lo:start],
        highlight=text[start : start + length],
        after=text[start + length : hi],
        trimmed_start=lo > 0,
        trimmed_end=hi < len(text),
    )


async def load(
    db: AsyncClient, user_id: UUID, document_id: UUID, chunk_id: UUID | None = None, page: int | None = None
) -> ViewerDoc:
    """본인 교안만 연다 (교안은 계정별로 나뉜다)."""
    try:
        res = (
            await db.table("documents")
            .select("id,title,course_name,week_no,source_type,raw_text")
            .eq("id", str(document_id))
            .eq("created_by", str(user_id))
            .limit(1)
            .execute()
        )
    except Exception:
        logger.exception("뷰어 교안 조회 실패")
        raise UpstreamError("교안을 불러오지 못했습니다.") from None
    if not res.data:
        raise NotFoundError("교안을 찾을 수 없습니다.")
    row: dict[str, Any] = res.data[0]

    chunk: dict[str, Any] | None = None
    if chunk_id is not None:
        try:
            found = (
                await db.table("document_chunks")
                .select("content,page_number")
                .eq("id", str(chunk_id))
                .eq("document_id", str(document_id))
                .limit(1)
                .execute()
            )
            chunk = found.data[0] if found.data else None
        except Exception:
            logger.warning("뷰어 청크 조회 실패 — 형광펜 없이 표시")
    if page is None and chunk:
        page = chunk.get("page_number")

    doc = ViewerDoc(
        id=document_id,
        title=row["title"],
        course_name=row.get("course_name"),
        week_no=row.get("week_no"),
        source_type=row.get("source_type") or "text",
        page=page,
    )
    if doc.source_type == "pdf":
        doc.has_pdf = await file_store.has_pdf(db, document_id)
        if doc.has_pdf:
            doc.highlight_text = (chunk["content"] if chunk else "")[:400]
            return doc
        doc.blocks = await _pdf_pages(db, document_id, chunk)
    else:
        doc.blocks = [_block(None, row.get("raw_text") or "", chunk["content"] if chunk else None)]
    doc.highlighted = any(b.highlight for b in doc.blocks)
    return doc


async def _pdf_pages(db: AsyncClient, document_id: UUID, chunk: dict[str, Any] | None) -> list[ViewerBlock]:
    """원본 PDF가 없을 때: 청크를 쪽 번호별로 이어 붙여 쪽마다 보여 준다."""
    try:
        res = (
            await db.table("document_chunks")
            .select("content,page_number,chunk_index")
            .eq("document_id", str(document_id))
            .order("chunk_index")
            .limit(400)
            .execute()
        )
    except Exception:
        logger.exception("뷰어 청크 목록 조회 실패")
        return []
    pages: dict[int | None, str] = {}
    for item in res.data or []:
        key = item.get("page_number")
        pages[key] = _merge_overlap(pages[key], item["content"]) if key in pages else item["content"]
    target = chunk.get("page_number") if chunk else None
    needle = chunk["content"] if chunk else None
    return [_block(p, text, needle if p == target else None) for p, text in pages.items()]
