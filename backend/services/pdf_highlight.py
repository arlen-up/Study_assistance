"""PDF 교안 한 쪽을 그림으로 그리고, 답변에 쓴 문장 부분에 노란 상자를 칠한다.

- 원본 PDF(bytes)와 쪽 번호, 답변 근거 문장을 받아 PNG를 돌려준다.
- 문장 위치를 찾지 못하면 상자 없이 쪽 전체를 보여 준다 (정확하지 않은 상자는 그리지 않는다).
"""

import logging
import re
from typing import Final

import pymupdf  # PyMuPDF

from backend.core.errors import BadRequestError

logger = logging.getLogger(__name__)

RENDER_DPI: Final[int] = 150
MAX_SNIPPETS: Final[int] = 6
MAX_SNIPPET_CHARS: Final[int] = 80
MAX_BOXES: Final[int] = 80
_SPLIT = re.compile(r"[\n\.\?!。]+")


def _snippets(text: str) -> list[str]:
    """근거 문장을 검색하기 좋은 짧은 조각들로 나눈다. 줄바꿈과 문장부호 기준."""
    parts = [" ".join(p.split()) for p in _SPLIT.split(text)]
    return [p[:MAX_SNIPPET_CHARS] for p in parts if len(p) >= 12][:MAX_SNIPPETS]


def render_page(data: bytes, page_no: int, text: str) -> tuple[bytes, int]:
    """(PNG 바이트, 칠한 상자 수)를 돌려준다."""
    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        logger.exception("PDF 열기 실패")
        raise BadRequestError("PDF 원본을 열 수 없어요.") from None
    try:
        if page_no < 1 or page_no > pdf.page_count:
            raise BadRequestError("요청한 쪽이 교안에 없어요.")
        page = pdf[page_no - 1]

        rects: list[pymupdf.Rect] = []
        for snippet in _snippets(text):
            for rect in page.search_for(snippet):
                if len(rects) >= MAX_BOXES:
                    break
                rects.append(rect)

        if rects:
            shape = page.new_shape()
            for rect in rects:
                shape.draw_rect(rect)
            shape.finish(color=(0.95, 0.55, 0.0), fill=(1.0, 0.9, 0.3), fill_opacity=0.35, width=1.5)
            shape.commit()

        pixmap = page.get_pixmap(dpi=RENDER_DPI, alpha=False)
        return pixmap.tobytes("png"), len(rects)
    finally:
        pdf.close()
