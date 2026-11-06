"""사용자 이름 규칙 (DB 없이 확인)."""

import pytest

from backend.services.profile_service import normalize, validation_error


@pytest.mark.parametrize("name", ["김새봄", "kim_2026", "Study_Overflow", "ab", "가나다라마바사아자차카타파하가나", "_kim_"])
def test_allowed(name: str) -> None:
    assert validation_error(normalize(name)) is None


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("", "입력"),
        ("a", "2~16자"),
        ("가나다라마바사아자차카타파하가나다", "2~16자"),
        ("kim-2026", "-"),
        ("김 새봄", "공백"),
        ("kim!", "!"),
        ("ㅋㅋㅋ", "ㅋ"),  # 자음만은 안 됨 (완성된 글자만)
        ("___", "밑줄"),
        ("kim😀", "😀"),
    ],
)
def test_rejected(name: str, reason: str) -> None:
    problem = validation_error(normalize(name))
    assert problem is not None and reason in problem


def test_normalize_trims_and_composes_hangul() -> None:
    decomposed = "김"  # '김'을 풀어쓴 글자 (맥 입력 등)
    assert normalize(f"  {decomposed}새봄 ") == "김새봄"
    assert validation_error(normalize(decomposed + "새봄")) is None
