"""학교 정보 질문 판별 테스트. 실행: python -m unittest discover -s tests -v"""

import unittest

from backend.services.school_info import SCHOOL_NOT_FOUND_ANSWER, is_meal_question, is_school_question


class SchoolInfoTest(unittest.TestCase):
    def test_school_questions(self) -> None:
        self.assertTrue(is_school_question("오늘 학식 메뉴 뭐야?"))
        self.assertTrue(is_school_question("장학금 신청 기간 알려줘"))
        self.assertTrue(is_school_question("소프트웨어융합과 학과 소개"))

    def test_study_questions_are_not_school(self) -> None:
        self.assertFalse(is_school_question("스택과 큐의 차이를 설명해 줘"))

    def test_not_found_answer_points_to_homepage(self) -> None:
        self.assertIn("확인되지 않아요, 홈페이지 링크를 봐 주세요", SCHOOL_NOT_FOUND_ANSWER)
        self.assertIn("https://www.hywoman.ac.kr/", SCHOOL_NOT_FOUND_ANSWER)


class MealQuestionTest(unittest.TestCase):
    def test_meal_questions(self) -> None:
        self.assertTrue(is_meal_question("오늘 학식 메뉴 뭐야?"))
        self.assertTrue(is_meal_question("이번 주 식단 알려줘"))

    def test_other_school_questions_are_not_meal(self) -> None:
        self.assertFalse(is_meal_question("장학금 신청 기간 알려줘"))


if __name__ == "__main__":
    unittest.main()
