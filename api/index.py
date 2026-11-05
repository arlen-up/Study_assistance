"""Vercel Python 서버리스 함수 엔트리 포인트.

vercel.json 이 모든 요청(/, /login, /ui/*, /api/*)을 이 파일로 보낸다.
public/ 폴더의 정적 파일(/static/*)은 Vercel CDN이 먼저 처리한다.

로컬 실행: uvicorn api.index:app --reload --port 8000
"""

import sys
from pathlib import Path

# Vercel/로컬 어디서 실행해도 프로젝트 루트의 backend 패키지를 import 할 수 있게 함
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.app import app  # noqa: E402

__all__ = ["app"]
