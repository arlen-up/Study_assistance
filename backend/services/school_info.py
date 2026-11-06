"""한양여대 학교 정보 질문 처리.

학생이 학식 메뉴, 장학금, 학과, 등록금 같은 학교 정보를 물으면:
- 학교 정보로 직접 등록해 둔 자료(교안 등록 화면에서 텍스트로 올린 자료)만 근거로 답한다.
- 그 자료에서 찾지 못하면 지어내지 않고 정해진 문장으로 홈페이지를 안내한다.
"""

from typing import Final

SCHOOL_HOME_URL: Final[str] = "https://www.hywoman.ac.kr/"
SCHOOL_NOT_FOUND_ANSWER: Final[str] = f"확인되지 않아요, 홈페이지 링크를 봐 주세요: {SCHOOL_HOME_URL}"

# 학교 정보 질문으로 보는 키워드
SCHOOL_KEYWORDS: Final[tuple[str, ...]] = (
    "학식",
    "식단",
    "메뉴",
    "장학",
    "학과",
    "등록금",
    "기숙사",
    "도서관",
    "입학",
    "학교 홈페이지",
    "한양여대",
)

# 학교 정보 답변 규칙 (AI에게 덧붙이는 지시)
SCHOOL_RULE: Final[str] = (
    "학교 정보 질문이다. 학교 정보로 등록된 자료만 근거로 답하고, 자료에 없는 내용은 절대 지어내지 마. "
    f"자료에서 찾지 못하면 정확히 '{SCHOOL_NOT_FOUND_ANSWER}'라고만 답해."
)


def is_school_question(text: str) -> bool:
    """학교 정보(학식·장학금·학과 등)를 묻는 질문인지 판단한다."""
    return any(keyword in text for keyword in SCHOOL_KEYWORDS)


# 학식(구내식당 메뉴) 질문은 일상 질문으로 분류한다
MEAL_KEYWORDS: Final[tuple[str, ...]] = ("학식", "식단", "학생식당")


def is_meal_question(text: str) -> bool:
    """학식(구내식당 메뉴) 질문인지 판단한다. 이런 질문은 일상 대화로 답한다."""
    return any(keyword in text for keyword in MEAL_KEYWORDS)
