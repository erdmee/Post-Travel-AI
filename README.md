# Post-Travel-AI

여행 사진을 정리하고 블로그 글로 만들어주는 AI 서비스.

여행을 다녀오면 수백 장의 사진이 남습니다. 그중 의미 있는 사진을 골라내고 흐름에 맞게 글로 풀어내는 일은 시간이 오래 걸립니다. Post-Travel-AI는 이 과정을 자동으로 처리합니다.

## 무엇을 해주나

사진 묶음을 입력하면 다음 세 가지를 수행합니다.

1. 사진 정리
   - 촬영 시간을 기준으로 사진을 묶어 장면 단위로 나눕니다 (예: 오전 카페, 점심 식사, 오후 산책).
   - 연사·중복샷처럼 거의 같은 사진은 한 장만 남깁니다.
   - 여행 전체를 대표하는 사진 약 10장을 추립니다.

2. 블로그 작성
   - 제목, 요약, 사진별 본문이 포함된 한국어 글 한 편을 생성합니다.
   - 글 스타일은 다섯 가지 중에서 선택할 수 있습니다.
     - `friendly_diary` — 친구에게 들려주듯 친근한 일기체
     - `emotional_essay` — 감성적인 에세이
     - `witty` — 위트 있고 가벼운 톤
     - `concise_log` — 짧고 담백한 기록
     - `magazine` — 잡지 기사 같은 정돈된 문체
   - 출력은 `{ title, summary, sections: [{ photoIds, text }] }` 형태의 JSON입니다. 어떤 본문이 어떤 사진과 짝지어지는지 명시되어 있어, 앱에서 사진과 텍스트를 함께 렌더링할 수 있습니다.

3. 장면 태그 부여
   - 각 사진을 `person`(인물), `landscape`(풍경), `food`(음식), `architecture`(건축물), `cityscape`(도시 풍경) 중 하나로 분류합니다.
   - 갤러리 필터링, 썸네일 선택 등에 활용할 수 있습니다.

## 시스템상의 역할

여행후유증 앱의 GPU AI 서버입니다. NestJS 백엔드 뒤에 위치하며 사진 분류와 블로그 생성 추론을 담당합니다. FastAPI 앱 하나를 Modal T4 GPU 컨테이너 위에 올려, HTTP 수신·CLIP 추론·Gemini 호출을 같은 프로세스에서 처리합니다.

## 엔드포인트 상세

### 1. 사진 분류 — `POST /vlm/analyze`

CLIP zero-shot 분류로 사진을 5개 라벨 중 하나로 매핑합니다.

- OpenAI CLIP ViT-B-32 (`laion2b_s34b_b79k`), 로컬 GPU 추론
- 외부 LLM API 호출 없음
- 한 장당 수십 ms (T4 기준)

### 2. 블로그 생성 — `POST /blog/generate`

사진과 메타데이터(`taken_at`, `lat/lng`, `scene_label`)를 받아 블로그를 작성합니다.

1. 대표 사진 선정 — `blog/dedup.py`
   - `taken_at` 기준 정렬 후, 30분 이상 간격이면 이벤트 분할
   - 각 이벤트 안에서 CLIP 임베딩 코사인 유사도 0.92 초과면 greedy dedup으로 제거
   - 남은 사진이 목표 개수(`target_count=10`)보다 많으면 시간순 균등 샘플링
2. Gemini 호출 — `blog/llm.py`
   - 이미지는 1024px JPEG로 리사이즈하여 토큰 절감
   - 응답 스키마: `{ title, summary, sections: [{ photoIds, text }] }`
   - `tenacity`로 5xx/429 지수 백오프 재시도 (최대 3회, 총 150초)

## 기술 스택

- FastAPI (Python 3.11+) — ASGI 앱, Modal `@modal.asgi_app()`로 노출
- PyTorch + open-clip-torch — CUDA / MPS / CPU 자동 선택
- pillow-heif — iPhone HEIC 디코드
- google-genai — Gemini SDK
- httpx — 사진 비동기 다운로드, 콜백 전송
- Modal — GPU 컨테이너 오케스트레이션

## 프로젝트 구조

```
Post-Travel-AI/
├── app.py                      Modal 진입점 (이미지 빌드, GPU 컨테이너 정의)
├── classifier/                 CLIP 분류기 (GPU 사용)
│   └── classify.py             _load() 싱글톤, classify(), embed_image()
├── blog/                       블로그 생성 파이프라인
│   ├── types.py                PhotoInput, BlogDraft TypedDict
│   ├── dedup.py                시간 분할 + CLIP 임베딩 dedup
│   ├── prompt.py               페르소나별 프롬프트 빌더
│   ├── llm.py                  Gemini 호출 + 응답 검증 + 재시도
│   └── generate.py             오케스트레이터
├── server/                     FastAPI 레이어
│   ├── main.py                 lifespan에서 CLIP 로드, 엔드포인트 정의
│   ├── schemas.py              요청·콜백 Pydantic 모델
│   ├── auth.py                 X-Internal-Token 검증
│   ├── downloader.py           사진 비동기 병렬 다운로드
│   └── tasks.py                백그라운드 처리 + 콜백 전송
├── requirements.txt
└── .env.example
```

### GPU 사용 지점

`classifier/classify.py:_load()`가 단일 진입점입니다. 두 곳에서 같은 모델과 텍스트 임베딩을 재사용합니다.

- `server/tasks.py:process_vlm_analyze` → `classify()` (이미지 임베딩 × 텍스트 임베딩 → softmax → argmax)
- `blog/dedup.py:select_representatives` → `embed_image()` (L2 정규화된 512-d CPU 텐서를 반환, dedup용 코사인 유사도 계산에 사용)

CLIP forward는 동기 함수이므로 `asyncio.to_thread`로 감싸 이벤트 루프를 막지 않습니다 (`server/tasks.py`).

## Local Development

### 사전 요구

- Python 3.11 이상 (3.13 권장)
- `GEMINI_API_KEY` — https://aistudio.google.com/apikey

### 셋업

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# .env 파일을 열어 GEMINI_API_KEY 입력

.venv/bin/uvicorn server.main:app --reload --port 8001
```

첫 실행 시 CLIP 가중치 약 350MB를 HuggingFace에서 내려받습니다 (5~10초). 이후에는 캐시됩니다. 맥에서는 MPS, 리눅스 GPU에서는 CUDA가 자동으로 선택됩니다.

### 확인

- `http://localhost:8001/health` → `{"status":"ok"}`
- `http://localhost:8001/docs` Swagger UI에서 엔드포인트를 직접 호출할 수 있습니다 (`X-Internal-Token` 헤더에 `.env`의 `GPU_INTERNAL_TOKEN` 값).

## 환경변수

| 이름 | 필수 | 기본값 | 비고 |
|---|---|---|---|
| `GEMINI_API_KEY` | yes | - | https://aistudio.google.com/apikey |
| `LLM_MODEL` | no | `gemini-2.5-flash` | `gemini-2.5-pro`, `gemini-3-flash-preview` 등 |
| `GPU_INTERNAL_TOKEN` | no | `dev-internal-token` | NestJS와 같은 값으로 공유. 인바운드 요청과 콜백 전송 모두에 사용. |

---

## Modal GPU 배포

[`app.py`](app.py)가 Modal 진입점이며, FastAPI 전체를 Modal T4 GPU 컨테이너에 그대로 올립니다. 주요 설계는 다음과 같습니다.

- CLIP 가중치를 이미지 빌드 시점에 포함합니다. `_download_clip_weights()`가 빌드 단계에서 실행되어 약 350MB가 컨테이너 레이어에 포함되므로, 콜드스타트 시 HuggingFace에서 다시 받지 않습니다.
- `@modal.enter()`에서 모델을 로드합니다. 컨테이너가 뜨면 즉시 CLIP을 GPU로 올리고 텍스트 임베딩을 계산해두기 때문에 첫 요청이 모델 로드 비용을 부담하지 않습니다.
- `@modal.concurrent(max_inputs=4)`로 한 컨테이너가 요청 4개를 동시 처리합니다. 사진 다운로드와 Gemini I/O 대기 동안 GPU 사용률을 끌어올립니다.
- `add_local_python_source`로 `classifier/`, `blog/`, `server/`만 컨테이너에 포함합니다. `.venv/`, `__pycache__/`, 테스트 파일은 제외됩니다.

### 1회 셋업

```bash
pip install modal
modal token new                  # 브라우저 로그인, 토큰 자동 저장
modal profile current            # 워크스페이스 및 크레딧 확인
```

Modal 대시보드에서 Secret을 생성합니다 (이름: `post-travel-ai-secrets`).

| 키 | 값 |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio 발급 키 |
| `GPU_INTERNAL_TOKEN` | NestJS와 같은 토큰 |
| `LLM_MODEL` | `gemini-3-flash-preview` 등 |

CLI로도 가능합니다.

```bash
modal secret create post-travel-ai-secrets \
  GEMINI_API_KEY=... \
  GPU_INTERNAL_TOKEN=... \
  LLM_MODEL=gemini-3-flash-preview
```

### 개발 (핫리로드, 임시 URL)

```bash
modal serve app.py
```

콘솔에 출력되는 `https://<workspace>--post-travel-ai-fastapi-app-dev.modal.run`으로 `/health`, `/docs`에 접근할 수 있습니다. 코드를 저장하면 자동으로 재배포됩니다.

### 프로덕션 배포

```bash
modal deploy app.py
```

고정 URL이 발급됩니다. NestJS의 `GPU_SERVER_URL`을 그 값으로 갱신합니다.

### 운영

```bash
modal app list
modal app logs post-travel-ai
modal app stop post-travel-ai     # 강제 종료
```

### GPU 노브 / 비용 가이드

[`app.py`](app.py)의 `@app.cls` 데코레이터가 운영 파라미터입니다.

| 옵션 | 현재 값 | 의미 |
|---|---|---|
| `gpu="T4"` | T4 | 약 $0.59/시간. CLIP ViT-B-32에는 충분합니다. 더 큰 비전 모델로 옮기려면 A10G/L4 고려. |
| `scaledown_window=600` | 10분 | 마지막 요청 이후 idle 상태로 컨테이너를 유지하는 시간. 짧을수록 저렴하지만 콜드스타트가 잦아집니다. |
| `min_containers=0` | 0 | 평소에는 컨테이너를 띄우지 않습니다. `1`로 두면 24/7 warm 상태이지만 GPU 시간이 계속 과금됩니다. |
| `max_containers=2` | 2 | 동시 GPU 컨테이너 상한. 트래픽이 몰려도 큐잉으로 비용을 제어합니다. |
| `@modal.concurrent(max_inputs=4)` | 4 | 컨테이너 하나가 동시에 처리하는 요청 수. I/O 대기 시간에 GPU 사용률을 높입니다. |
| `timeout=600` | 10분 | 단일 요청 최대 처리 시간. 블로그 생성과 Gemini 재시도까지 포함한 여유 값입니다. |

### 콜드스타트 비용

- 컨테이너 부팅: 수 초 (Modal 인프라)
- `@modal.enter()` 모델 로드: T4에서 약 2~3초
- 가중치 다운로드: 없음 (이미지에 포함됨)

지속적인 트래픽이 있을 때는 `min_containers=1`로 warm pool을 유지하는 것이 사용자 체감 응답에 가장 큰 영향을 줍니다.