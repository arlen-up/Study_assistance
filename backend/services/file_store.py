"""교안 원본 파일(PDF) 보관 — 뷰어에서 출처 쪽(page)을 바로 띄우기 위해 쓴다.

- Supabase Storage의 비공개 버킷(lesson-files)에 `<교안 ID>.pdf`로 저장한다.
- 버킷은 서버(secret 키)만 읽고 쓴다. 브라우저는 로그인 확인을 거친 /ui/viewer/<ID>/file 로만 받는다.
- 버킷이 없으면 처음 올릴 때 만든다. 저장에 실패해도 교안 등록(검색용 글자)은 그대로 성공시킨다.
"""

import logging
from uuid import UUID

from storage3.exceptions import StorageException
from supabase import AsyncClient

logger = logging.getLogger(__name__)

BUCKET = "lesson-files"
MAX_PDF_BYTES = 10_000_000


def _path(document_id: UUID | str) -> str:
    return f"{UUID(str(document_id))}.pdf"


async def _ensure_bucket(db: AsyncClient) -> None:
    try:
        await db.storage.get_bucket(BUCKET)
    except StorageException:
        await db.storage.create_bucket(
            BUCKET,
            options={"public": False, "file_size_limit": MAX_PDF_BYTES, "allowed_mime_types": ["application/pdf"]},
        )
        logger.info("Storage 버킷을 만들었습니다: %s", BUCKET)


async def save_pdf(db: AsyncClient, document_id: UUID, data: bytes) -> bool:
    """원본 PDF를 저장한다. 성공하면 True (실패해도 예외를 던지지 않는다)."""
    options = {"content-type": "application/pdf", "upsert": "true", "cache-control": "3600"}
    for attempt in range(2):
        try:
            await db.storage.from_(BUCKET).upload(_path(document_id), data, options)  # type: ignore[arg-type]
            return True
        except StorageException as exc:
            if attempt == 0:
                logger.info("PDF 저장 재시도 (버킷 확인): %s", type(exc).__name__)
                try:
                    await _ensure_bucket(db)
                except StorageException:
                    logger.exception("Storage 버킷 준비 실패")
                    return False
                continue
            logger.exception("PDF 원본 저장 실패")
        except Exception:
            logger.exception("PDF 원본 저장 중 알 수 없는 오류")
            return False
    return False


async def load_pdf(db: AsyncClient, document_id: UUID) -> bytes | None:
    """저장된 원본 PDF. 없거나 읽지 못하면 None."""
    try:
        return await db.storage.from_(BUCKET).download(_path(document_id))
    except StorageException:
        return None
    except Exception:
        logger.exception("PDF 원본 읽기 실패")
        return None


async def has_pdf(db: AsyncClient, document_id: UUID) -> bool:
    try:
        files = await db.storage.from_(BUCKET).list(None, {"search": str(document_id), "limit": 5})
    except Exception:
        return False
    return any(f.get("name") == _path(document_id) for f in files or [])
