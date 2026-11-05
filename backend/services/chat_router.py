"""챗봇 질문 유형 분류 (Router).

세 유형으로 나눈다.
- chat : 일상 대화 (인사, 감사, 잡담). 교안 검색을 하지 않고, "[근거 부족]"을 띄우지 않는다.
- study: 학습 질문. 교안을 검색(RAG)해서 답하고, 근거가 없으면 "[근거 부족]"을 띄운다.
- info : 학적 정보 질문 (학점, 이수, 수강, 성적, 졸업, 미제출 과제 등).
         LMS 도구로 확인한 내용만 답하고, 교안 검색과 "[근거 부족]"은 쓰지 않는다.

외부 호출 없이 키워드로만 판단하므로 빠르고 테스트하기 쉽다.
판단이 애매하면 study로 보낸다(학습 도우미의 기본 동작).
"""

from typing import Literal

Intent = Literal["chat", "study", "info"]

# 학적 정보 조회 키워드 (LMS 도구로 답해야 하는 질문)
INFO_KEYWORDS: tuple[str, ...] = (
    "학점",
    "이수",
    "수강",
    "성적",
    "졸업",
    "성적표",
    "학사",
    "시간표",
    "미제출",
    "마감",
    "전공 과목",
    "내 과목",
)

# 일상 대화 키워드 (짧은 잡담에만 적용)
CHAT_KEYWORDS: tuple[str, ...] = (
    "안녕",
    "반가",
    "고마",
    "감사",
    "잘 가",
    "잘가",
    "뭐해",
    "넌 누구",
    "너는 누구",
    "누구야",
    "ㅎㅎ",
    "ㅋㅋ",
)

# 일상 대화로 보기 위한 최대 길이 (긴 질문은 학습 질문일 가능성이 높다)
CHAT_MAX_LENGTH = 30


def classify_intent(message: str) -> Intent:
    """학생 질문을 chat / study / info 중 하나로 분류한다."""
    text = message.strip()
    if not text:
        return "chat"
    if any(keyword in text for keyword in INFO_KEYWORDS):
        return "info"
    if len(text) <= CHAT_MAX_LENGTH and any(keyword in text for keyword in CHAT_KEYWORDS):
        return "chat"
    return "study"
