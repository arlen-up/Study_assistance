"""챗봇 Router 분류 테스트. 실행: python -m unittest discover -s tests -v"""

import unittest

from backend.services.chat_router import classify_intent


class ClassifyIntentTest(unittest.TestCase):
    def test_info_questions(self) -> None:
        self.assertEqual(classify_intent("내 이수 학점 몇 학점이야?"), "info")
        self.assertEqual(classify_intent("졸업까지 얼마나 남았어"), "info")
        self.assertEqual(classify_intent("이번 학기 수강 과목 알려줘"), "info")

    def test_chat_questions(self) -> None:
        self.assertEqual(classify_intent("안녕하세요"), "chat")
        self.assertEqual(classify_intent("고마워 ㅎㅎ"), "chat")
        self.assertEqual(classify_intent("넌 누구야?"), "chat")

    def test_study_questions(self) -> None:
        self.assertEqual(classify_intent("스택과 큐의 차이를 설명해 줘"), "study")
        self.assertEqual(classify_intent("저번 주 자바 내용 알려줘"), "study")

    def test_long_greeting_is_not_chat(self) -> None:
        long_text = "안녕하세요, 제가 어제 배운 자바 상속 개념을 다시 정리하고 싶은데 도와주실 수 있을까요"
        self.assertEqual(classify_intent(long_text), "study")

    def test_empty_is_chat(self) -> None:
        self.assertEqual(classify_intent("   "), "chat")

    def test_info_wins_over_chat(self) -> None:
        self.assertEqual(classify_intent("안녕, 내 학점 알려줘"), "info")


if __name__ == "__main__":
    unittest.main()
