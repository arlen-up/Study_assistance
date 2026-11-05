"""플래시카드·마인드맵 후처리: 출처 검증, 학습 기록, 마인드맵 SVG 배치 계산."""

from uuid import UUID

from supabase import AsyncClient

from backend.schemas.models import SourceChunk
from backend.schemas.study_tools import (
    Flashcard,
    FlashcardDeckDraft,
    MindEdge,
    MindMapDraft,
    MindNode,
)
from backend.services import quiz_service

# ---------------------------------------------------------------------
# 플래시카드
# ---------------------------------------------------------------------
def _norm(text: str) -> str:
    """비교용 정규화: 공백·문장부호를 없애고 소문자로."""
    return "".join(ch for ch in text.lower() if ch.isalnum())



def build_cards(draft: FlashcardDeckDraft, retrieved: dict[str, SourceChunk]) -> list[Flashcard]:
    """앞면·뒷면이 겹치는 카드 제거 + 실제로 검색된 청크만 출처로 인정."""
    cards: list[Flashcard] = []
    seen: set[str] = set()
    for item in draft.cards:
        keys = {_norm(item.front), _norm(item.back)}
        if keys & seen:
            continue
        seen |= keys
        source = retrieved.get(item.source_chunk_id or "")
        cards.append(Flashcard(index=len(cards), front=item.front, back=item.back, hint=item.hint, source=source))
    return cards


async def record_flashcard_session(
    db: AsyncClient, user_id: UUID, document_id: UUID, known: int, total: int, duration_seconds: int | None
) -> UUID:
    """플래시카드 한 판도 학습 완료로 기록 → 잔디·펫·복습 일정에 반영."""
    return await quiz_service.record_study_log(
        db,
        user_id=user_id,
        document_id=document_id,
        quiz_id=None,
        score=known,
        total=total,
        duration_seconds=duration_seconds,
    )


# ---------------------------------------------------------------------
# 마인드맵 배치 (가로 트리: 중심 → 핵심 개념 → 세부 개념)
# ---------------------------------------------------------------------
NODE_HEIGHT = 34.0
LEAF_GAP = 12.0
BRANCH_GAP = 22.0
COLUMN_GAP = 56.0
PADDING = 20.0
CHAR_WIDTH = 13.5   # 한글 13px 글꼴 기준 대략적인 글자 폭
MIN_WIDTH = 64.0
MAX_WIDTH = 210.0


def _text_width(label: str) -> float:
    units = sum(1.0 if ord(ch) > 0x2E80 else 0.6 for ch in label)  # 한글·한자는 넓게, 영문·숫자는 좁게
    return max(MIN_WIDTH, min(MAX_WIDTH, units * CHAR_WIDTH + 26))


def _curve(x1: float, y1: float, x2: float, y2: float) -> str:
    mid = (x1 + x2) / 2
    return f"M{x1:.1f},{y1:.1f} C{mid:.1f},{y1:.1f} {mid:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"


def layout_mindmap(draft: MindMapDraft) -> tuple[list[MindNode], list[MindEdge], float, float]:
    root_w = _text_width(draft.root)
    branch_w = max((_text_width(b.label) for b in draft.branches), default=MIN_WIDTH)
    leaf_w = max((_text_width(c.label) for b in draft.branches for c in b.children), default=0.0)

    x_root = PADDING
    x_branch = x_root + root_w + COLUMN_GAP
    x_leaf = x_branch + branch_w + COLUMN_GAP

    nodes: list[MindNode] = []
    edges: list[MindEdge] = []
    y = PADDING
    branch_centers: list[tuple[MindNode, list[MindNode]]] = []

    for bi, branch in enumerate(draft.branches):
        leaves: list[MindNode] = []
        for ci, child in enumerate(branch.children):
            leaves.append(MindNode(
                id=f"n{bi}-{ci}", label=child.label, level=2,
                x=x_leaf, y=y, width=_text_width(child.label), height=NODE_HEIGHT,
            ))
            y += NODE_HEIGHT + LEAF_GAP
        if leaves:
            center = (leaves[0].y + leaves[-1].y) / 2
            y += BRANCH_GAP - LEAF_GAP
        else:
            center = y
            y += NODE_HEIGHT + BRANCH_GAP
        b_node = MindNode(
            id=f"n{bi}", label=branch.label, level=1,
            x=x_branch, y=center, width=_text_width(branch.label), height=NODE_HEIGHT,
        )
        branch_centers.append((b_node, leaves))

    content_bottom = y - BRANCH_GAP + PADDING
    root_y = (branch_centers[0][0].y + branch_centers[-1][0].y) / 2 if branch_centers else PADDING
    root = MindNode(id="root", label=draft.root, level=0, x=x_root, y=root_y, width=root_w, height=NODE_HEIGHT + 8)
    nodes.append(root)

    for b_node, leaves in branch_centers:
        nodes.append(b_node)
        edges.append(MindEdge(
            path=_curve(root.x + root.width, root.y + root.height / 2, b_node.x, b_node.y + NODE_HEIGHT / 2),
            level=1,
        ))
        for leaf in leaves:
            nodes.append(leaf)
            edges.append(MindEdge(
                path=_curve(b_node.x + b_node.width, b_node.y + NODE_HEIGHT / 2, leaf.x, leaf.y + NODE_HEIGHT / 2),
                level=2,
            ))

    right = max(n.x + n.width for n in nodes) + PADDING
    width = max(right, x_leaf + leaf_w + PADDING if leaf_w else right)
    height = max(content_bottom, root.y + root.height + PADDING)
    return nodes, edges, round(width, 1), round(height, 1)
