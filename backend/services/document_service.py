"""교안 파싱·청크 분할·DB 적재.

- 업로드 파일(PDF/Markdown/HTML/Text) 또는 허용된 LMS 호스트의 URL을 텍스트로 변환
- 페이지 정보를 유지한 채 청크로 나눠 출처(page_number)를 표시할 수 있게 함
"""

import io
import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

import httpx
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from supabase import AsyncClient

from backend.core.config import Settings
from backend.core.errors import BadRequestError, PayloadTooLargeError, UpstreamError
from backend.schemas.models import DocumentIngestResponse
from backend.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

SourceType = Literal["pdf", "markdown", "html", "text"]

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
INSERT_BATCH = 100


@dataclass(frozen=True)
class ParsedPage:
    page_number: int | None
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    source_type: SourceType
    pages: list[ParsedPage]

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)


@dataclass(frozen=True)
class ChunkDraft:
    chunk_index: int
    content: str
    page_number: int | None


# ---------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "pre", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        lines = (line.strip() for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BadRequestError("텍스트 인코딩을 인식할 수 없습니다. UTF-8로 저장해 주세요.")


def _parse_pdf(data: bytes) -> list[ParsedPage]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise BadRequestError("암호가 걸린 PDF는 처리할 수 없습니다.")
        pages = [ParsedPage(i + 1, (page.extract_text() or "").strip()) for i, page in enumerate(reader.pages)]
    except PdfReadError:
        raise BadRequestError("PDF 파일을 읽을 수 없습니다.") from None
    if not any(p.text for p in pages):
        raise BadRequestError("PDF에서 텍스트를 추출하지 못했습니다. 스캔 이미지 PDF는 지원하지 않습니다.")
    return pages


def detect_source_type(filename: str | None, content_type: str | None) -> SourceType:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or "pdf" in ctype:
        return "pdf"
    if name.endswith((".html", ".htm")) or "html" in ctype:
        return "html"
    if name.endswith((".md", ".markdown")) or "markdown" in ctype:
        return "markdown"
    if name.endswith(".txt") or ctype.startswith("text/") or not name:
        return "text"
    raise BadRequestError("지원하지 않는 파일 형식입니다. PDF, Markdown, HTML, TXT만 가능합니다.")


def parse_bytes(data: bytes, source_type: SourceType) -> ParsedDocument:
    if not data:
        raise BadRequestError("빈 파일입니다.")
    if source_type == "pdf":
        return ParsedDocument("pdf", _parse_pdf(data))
    text = _decode_text(data)
    if source_type == "html":
        extractor = _TextExtractor()
        extractor.feed(text)
        text = extractor.text()
    text = text.strip()
    if not text:
        raise BadRequestError("문서에 텍스트가 없습니다.")
    return ParsedDocument(source_type, [ParsedPage(None, text)])


def parse_plain_text(text: str) -> ParsedDocument:
    text = text.strip()
    if not text:
        raise BadRequestError("교안 텍스트가 비어 있습니다.")
    return ParsedDocument("text", [ParsedPage(None, text)])


# ---------------------------------------------------------------------
# URL 가져오기 (SSRF 방지: https + 허용 호스트 + 리다이렉트 금지 + 크기 제한)
# ---------------------------------------------------------------------
async def fetch_remote_document(url: str, settings: Settings) -> ParsedDocument:
    allowed = settings.allowed_hosts
    if not allowed:
        raise BadRequestError("URL로 교안을 가져오는 기능이 비활성화되어 있습니다 (LMS_ALLOWED_HOSTS 미설정).")

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise BadRequestError("https:// 주소만 허용됩니다.")
    if host not in allowed:
        raise BadRequestError(f"허용되지 않은 호스트입니다: {host}")

    limit = settings.max_upload_bytes
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            async with client.stream("GET", url, headers={"User-Agent": "lms-study-helper/0.1"}) as resp:
                if resp.status_code != 200:
                    raise UpstreamError(f"교안 URL 응답 코드: {resp.status_code}")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > limit:
                        raise PayloadTooLargeError("교안 파일이 너무 큽니다.")
                content_type = resp.headers.get("content-type")
    except httpx.HTTPError:
        raise UpstreamError("교안 URL을 가져오지 못했습니다.") from None

    source_type = detect_source_type(parsed.path, content_type)
    return parse_bytes(bytes(buf), source_type)


# ---------------------------------------------------------------------
# 청크 분할 & 적재
# ---------------------------------------------------------------------
def chunk_document(doc: ParsedDocument) -> list[ChunkDraft]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", "。", " ", ""],
    )
    chunks: list[ChunkDraft] = []
    for page in doc.pages:
        if not page.text:
            continue
        for piece in splitter.split_text(page.text):
            piece = piece.strip()
            if piece:
                chunks.append(ChunkDraft(len(chunks), piece, page.page_number))
    if not chunks:
        raise BadRequestError("분할할 텍스트가 없습니다.")
    return chunks


async def ingest_document(
    db: AsyncClient,
    embeddings: EmbeddingService,
    *,
    title: str,
    parsed: ParsedDocument,
    created_by: UUID,
    course_name: str | None = None,
    week_no: int | None = None,
    keywords: list[str] | None = None,
    source_url: str | None = None,
) -> DocumentIngestResponse:
    chunks = chunk_document(parsed)
    vectors = await embeddings.embed_documents([c.content for c in chunks])
    full_text = parsed.full_text

    try:
        doc_res = (
            await db.table("documents")
            .insert(
                {
                    "title": title,
                    "course_name": course_name,
                    "week_no": week_no,
                    "source_type": parsed.source_type,
                    "source_url": source_url,
                    "raw_text": full_text,
                    "keywords": keywords or [],
                    "created_by": str(created_by),
                }
            )
            .execute()
        )
    except Exception:
        logger.exception("documents insert 실패")
        raise UpstreamError("교안 저장에 실패했습니다.") from None

    document_id: str = doc_res.data[0]["id"]
    rows: list[dict[str, Any]] = [
        {
            "document_id": document_id,
            "chunk_index": c.chunk_index,
            "content": c.content,
            "page_number": c.page_number,
            "metadata": {"char_count": len(c.content), "source_type": parsed.source_type},
            "embedding": vec,
        }
        for c, vec in zip(chunks, vectors, strict=True)
    ]

    try:
        for start in range(0, len(rows), INSERT_BATCH):
            await db.table("document_chunks").insert(rows[start : start + INSERT_BATCH]).execute()
    except Exception:
        logger.exception("document_chunks insert 실패 → 문서 롤백")
        # PostgREST는 여러 요청을 하나의 트랜잭션으로 묶지 않으므로 보상 삭제 (chunks는 cascade)
        try:
            await db.table("documents").delete().eq("id", document_id).execute()
        except Exception:
            logger.exception("롤백 삭제 실패: document_id=%s", document_id)
        raise UpstreamError("교안 청크 저장에 실패했습니다.") from None

    return DocumentIngestResponse(
        document_id=UUID(document_id), title=title, chunk_count=len(chunks), char_count=len(full_text)
    )


def html_to_text(markup: str) -> str:
    """HTML(예: Moodle 페이지 본문)을 스크립트·스타일을 제외한 순수 텍스트로 바꾼다."""
    extractor = _TextExtractor()
    extractor.feed(markup or "")
    return extractor.text()
