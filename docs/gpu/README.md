# 교내 GPU(틸론) 활용 증빙

## 한 줄 요약
틸론(교내 GPU 실습 환경)의 JupyterLab에서 **SDXL-Turbo**(텍스트→이미지 모델)로 로고 이미지를 만들고, 서비스 헤더와 로그인 화면에 적용했습니다.

## 파일
- `workshop.ipynb`: 실습 노트북 (이미지 생성 셀 `sdxl_generate` 포함)
- `logo_applied.png`: 생성 후 서비스에 적용한 로고 (`public/static/img/logo.png`와 동일)

## 생성 설정
- 실행 환경: 틸론 JupyterLab 실습 노트북, GPU 사용
- 모델: SDXL-Turbo (`stabilityai/sdxl-turbo`, 노트북 3-1 칸)
- 프롬프트 3개 (노트북 3-3 칸에 입력)
  1. `iconic flat laptop icon opened to about 110 degrees, bold solid navy silhouette, rounded corners, light blue screen with the blue text LSSA in the center, minimal glyph style, no people, white background`
  2. `simple bold laptop app icon, navy base, light blue screen with blue letters lsa in the middle, flat geometric shapes, no people, white background`
  3. `minimal iconic laptop symbol opened to 110 degrees, thick simple outline, pale blue screen with bold blue text lsa, clean flat design, no people, white background`
- 시드 `5, 12, 30`, 단계 `4`, 해상도 512×512

## 참고
- 프롬프트 기록은 대회 홈페이지에서 따로 관리합니다.
- 서비스 답변의 AI는 외부 OpenAI 호환 API를 사용했고, 이미지 생성에만 틸론 GPU 환경을 사용했습니다.
- 생성 결과 파일 원본(`results/sdxl/*.png`, `*.json`)은 실습 서버에 있어 이 저장소에는 `logo_applied.png` 한 장만 올렸습니다.
