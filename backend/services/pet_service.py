"""가상 펫 (게이미피케이션).

🎨 펫 컨셉(이름·성장 단계·이모지·그림·문구)은 이 파일의 PetTheme 하나에 모여 있다.
   컨셉을 바꾸려면 새 PetTheme을 만들고 ACTIVE_THEME만 바꾸면 된다. (DB에는 펫 이름만 저장)

상태 계산
- 성장 단계: 현재 연속 학습일(streak)로 결정
- 기분: 오늘 공부함(happy) / 어제까지 이어짐(waiting) / 끊김(sad) / 아직 시작 안 함(new)
"""

from dataclasses import dataclass, field

from backend.schemas.models import PetMood, PetStageInfo, PetStatus, StreakResponse


@dataclass(frozen=True)
class PetStage:
    key: str
    min_streak: int   # 이 연속 학습일부터 이 단계
    emoji: str
    label: str
    image: str = ""   # 그림 주소 (public/static/img/...). 비우면 이모지로 보여줌


@dataclass(frozen=True)
class PetTheme:
    key: str
    default_name: str
    stages: tuple[PetStage, ...]
    # {name}=펫 이름, {이가}=조사, {streak}=연속일, {next}=다음 연속일
    mood_messages: dict[str, str] = field(default_factory=dict)
    notify_title: str = "{name}{이가} 기다려요"


BUTTERFLY = PetTheme(
    key="butterfly",
    default_name="나비",
    stages=(
        PetStage("egg", 0, "🥚", "알", "/static/img/pet/egg.svg"),
        PetStage("larva", 1, "🐛", "애벌레", "/static/img/pet/larva.svg"),
        PetStage("pupa", 3, "🌰", "번데기", "/static/img/pet/pupa.svg"),
        PetStage("butterfly", 7, "🦋", "나비", "/static/img/pet/butterfly.svg"),
    ),
    mood_messages={
        "new": "첫 퀴즈를 풀면 {name}{이가} 알에서 깨어나요!",
        "happy": "오늘도 공부 완료! {name}{이가} 신나서 날갯짓해요. ({streak}일 연속)",
        "waiting": "{name}{이가} 기다려요. 오늘 3분만 공부하면 {next}일 연속이에요!",
        "sad": "{name}{이가} 조금 시무룩해요. 오늘 다시 시작해 볼까요?",
    },
    notify_title="🦋 {name}{이가} 기다려요",
)

# ✏️ 컨셉 변경 지점
ACTIVE_THEME: PetTheme = BUTTERFLY


def _has_batchim(word: str) -> bool:
    """마지막 글자에 받침이 있는지 (한글이 아니면 받침 없음으로 본다)."""
    if not word:
        return False
    code = ord(word[-1]) - 0xAC00
    return 0 <= code <= 11171 and code % 28 != 0


def with_ro(word: str) -> str:
    """'나비' → '나비로', '알' → '알로', '번데기' → '번데기로', '책' → '책으로' (ㄹ받침은 '로')."""
    if not word:
        return word
    code = ord(word[-1]) - 0xAC00
    rieul = 0 <= code <= 11171 and code % 28 == 8
    return f"{word}{'으로' if _has_batchim(word) and not rieul else '로'}"


def fill(template: str, name: str, **values: object) -> str:
    """'{name}{이가}' 같은 템플릿을 받침에 맞는 조사로 채운다."""
    josa = {"이가": "이" if _has_batchim(name) else "가", "은는": "은" if _has_batchim(name) else "는"}
    return template.format(name=name, **josa, **values)


def pet_status(streak: StreakResponse, pet_name: str | None, theme: PetTheme = ACTIVE_THEME) -> PetStatus:
    name = (pet_name or "").strip() or theme.default_name
    current = streak.current_streak
    studied_today = bool(streak.days) and streak.days[-1].completed

    if streak.active_days == 0:
        mood: PetMood = "new"
    elif studied_today:
        mood = "happy"
    elif current > 0:
        mood = "waiting"
    else:
        mood = "sad"

    stage = theme.stages[0]
    for s in theme.stages:
        if current >= s.min_streak:
            stage = s
    following = next((s for s in theme.stages if s.min_streak > current), None)
    progress = 100
    if following is not None:
        span = max(1, following.min_streak - stage.min_streak)
        progress = min(100, (current - stage.min_streak) * 100 // span)

    return PetStatus(
        name=name,
        theme=theme.key,
        stage_key=stage.key,
        stage_label=stage.label,
        emoji=stage.emoji,
        image_url=stage.image or None,
        stages=[
            PetStageInfo(key=s.key, label=s.label, emoji=s.emoji, image_url=s.image or None,
                         min_streak=s.min_streak, reached=current >= s.min_streak, current=s is stage)
            for s in theme.stages
        ],
        mood=mood,
        message=fill(theme.mood_messages[mood], name, streak=current, next=current + 1),
        current_streak=current,
        next_stage_label=following.label if following else None,
        days_to_next=(following.min_streak - current) if following else None,
        progress_percent=progress,
    )


def notify_title(pet_name: str | None, theme: PetTheme = ACTIVE_THEME) -> str:
    return fill(theme.notify_title, (pet_name or "").strip() or theme.default_name)
