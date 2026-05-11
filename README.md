# Post-Travel-AI

여행후유증 앱의 AI 서버. NestJS 백엔드의 GPU 서버 역할로, 사진 분류와 여행 블로그 자동 생성을 담당.

## 기능

### 1. 사진 분류 (CLIP zero-shot)

업로드된 사진을 5개 라벨 중 하나로 분류:
`person`, `landscape`, `food`, `architecture`, `cityscape`

- OpenAI CLIP ViT-B-32 모델, 로컬 추론 (Apple MPS / CUDA / CPU)
- 외부 LLM API 사용 없음
- 사진 한 장당 수십~수백 ms

### 2. 여행 블로그 생성 (Gemini)

여러 사진과 메타데이터(시간, GPS, 분류 라벨)를 받아 한국어 블로그를 자동 작성.

- 시간 기반 이벤트 분할 + CLIP 임베딩 시각 유사도 dedup → 대표 사진 N장 선정
- Google Gemini 멀티모달 API 호출 (이미지 1024px JPEG로 리사이즈해 토큰 절감)
- 5종 페르소나 지원: `friendly_diary`, `emotional_essay`, `witty`, `concise_log`, `magazine`
- 출력은 `{ title, summary, sections: [{ photoIds, text }] }` 구조의 JSON

## 기술 스택

- FastAPI (Python 3.11+)
- PyTorch + open-clip-torch (CLIP)
- pillow-heif (iPhone HEIC 지원)
- google-genai (Gemini SDK)
- tenacity (Gemini 5xx/429 재시도)
- httpx (비동기 다운로드 / 콜백 전송)

## 프로젝트 구조

```
Post-Travel-AI/
├── classifier/                CLIP 분류기
│   ├── classify.py            classify(), embed_image()
│   └── __init__.py
├── blog/                      블로그 생성 파이프라인
│   ├── types.py               PhotoInput, BlogDraft TypedDict
│   ├── dedup.py               시간 + 시각 유사도 dedup
│   ├── prompt.py              페르소나별 프롬프트 빌더
│   ├── llm.py                 Gemini 호출 + 응답 검증 + 재시도
│   ├── generate.py            오케스트레이터
│   └── __init__.py
├── server/                    FastAPI 엔드포인트
│   ├── main.py                /vlm/analyze, /blog/generate, /health
│   ├── schemas.py             요청·콜백 Pydantic 모델
│   ├── auth.py                X-Internal-Token 검증
│   ├── downloader.py          비동기 사진 다운로드
│   ├── tasks.py               백그라운드 처리 + 콜백 전송
│   └── __init__.py
├── requirements.txt
├── .env.example
├── README.md                  (이 파일)
└── BACKEND_INTEGRATION.md     NestJS 백엔드 통합 명세
```

## Local Development

### 사전 요구

- Python 3.11 이상 (3.13 권장)
- `GEMINI_API_KEY`

### 셋업

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# .env 열어서 GEMINI_API_KEY 값 입력

.venv/bin/uvicorn server.main:app --reload --port 8001
```

첫 실행 시 CLIP 모델 약 350MB가 HuggingFace에서 다운로드됨 (5~10초). 이후엔 캐시됨.

### 확인

- `http://localhost:8001/health` 응답이 `{"status":"ok"}`
- `http://localhost:8001/docs` 에서 Swagger UI로 엔드포인트 직접 테스트 가능
  (`X-Internal-Token` 헤더에 `.env`의 `GPU_INTERNAL_TOKEN` 값 입력)

## 환경변수

| 이름 | 필수 | 기본값 | 비고 |
|---|---|---|---|
| `GEMINI_API_KEY` | yes | - | https://aistudio.google.com/apikey 에서 발급 |
| `LLM_MODEL` | no | `gemini-2.5-flash` | `gemini-2.5-flash`, `gemini-2.5-pro` 등 |
| `GPU_INTERNAL_TOKEN` | no | `dev-internal-token` | NestJS와 같은 값으로 공유 |

## 백엔드와의 연동

이 서버는 NestJS 백엔드의 `GPU_SERVER_URL`이 가리키는 곳에 떠 있어야 함.

API 명세, 콜백 형식, 백엔드 측에서 필요한 작업은 [BACKEND_INTEGRATION.md](BACKEND_INTEGRATION.md) 참조.
