"""게이미피케이션 스트릭(잔디 심기).

DB 함수 `get_study_heatmap`이 최근 N일의 날짜별 학습 여부를 KST 기준으로 빠짐없이 돌려준다.
여기서는 현재 연속 학습일·최장 연속 학습일을 계산한다.
"""

import logging
from typing import Any
from uuid import UUID

from supabase import AsyncClient

from backend.core.errors import UpstreamError
from backend.schemas.models import HeatmapDay, StreakResponse

logger = logging.getLogger(__name__)


def compute_streaks(days: list[HeatmapDay]) -> tuple[int, int]:
    """(현재 스트릭, 최장 스트릭). days는 날짜 오름차순이며 마지막 원소가 오늘.

    오늘 아직 학습하지 않았어도 어제까지 이어졌다면 스트릭을 유지한다(GitHub 방식).
    """
    longest = run = 0
    for day in days:
        run = run + 1 if day.completed else 0
        longest = max(longest, run)

    current = 0
    tail = days if not days or days[-1].completed else days[:-1]
    for day in reversed(tail):
        if not day.completed:
            break
        current += 1
    return current, longest


async def get_streak(db: AsyncClient, user_id: UUID, days: int = 365) -> StreakResponse:
    try:
        res = await db.rpc("get_study_heatmap", {"p_user_id": str(user_id), "p_days": days}).execute()
    except Exception:
        logger.exception("get_study_heatmap 실패")
        raise UpstreamError("학습 기록을 불러오지 못했습니다.") from None

    rows: list[dict[str, Any]] = res.data or []
    heatmap = [
        HeatmapDay(date=row["day"], completed=bool(row["is_completed"]), count=int(row["session_count"]))
        for row in rows
    ]
    current, longest = compute_streaks(heatmap)
    return StreakResponse(
        days=heatmap,
        current_streak=current,
        longest_streak=longest,
        active_days=sum(d.completed for d in heatmap),
        total_days=len(heatmap),
    )
