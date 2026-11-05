"""가상 LMS 모의 서버.

LMS 도우미와 독립된 FastAPI 앱이다. 도우미의 코드(backend 패키지)를 import 하지 않으며,
도우미는 HTTP API(/lms/api/*)로만 이 서버와 통신한다.
"""

__version__ = "0.1.0"
