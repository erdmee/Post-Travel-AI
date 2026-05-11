# Backend Integration Spec

NestJS 백엔드 ↔ FastAPI AI 서버 연동을 위한 인터페이스 명세.

## Local Development Setup (백엔드 통합 테스트용)

### 사전 요구
- Python 3.11 이상 (3.13 권장)
- `GEMINI_API_KEY` — 팀 내부 채널로 따로 전달 (깃에 안 올라감)

### 셋업
```bash
# 1. 클론
git clone <이 레포 URL>
cd Post-Travel-AI

# 2. 가상환경 + 의존성 (첫 실행 5~10분, torch 등 ~2GB)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 환경변수 파일
cp .env.example .env
# .env 열어서 GEMINI_API_KEY=<팀에서 받은 값> 입력
# GPU_INTERNAL_TOKEN은 디폴트(dev-internal-token) 그대로 OK
# (NestJS의 .env에도 같은 값으로 설정해야 함)

# 4. 서버 실행
.venv/bin/uvicorn server.main:app --reload --port 8001
```

첫 실행 시 CLIP 모델 ~350MB가 HuggingFace에서 다운로드됨 (5~10초). 이후엔 캐시됨.

### 확인
- `http://localhost:8001/health` → `{"status":"ok"}`
- `http://localhost:8001/docs` → Swagger UI. 엔드포인트 직접 호출 가능 (X-Internal-Token 헤더에 `dev-internal-token` 입력)

### NestJS에서 호출하기
자기 NestJS `.env`:
```
GPU_SERVER_URL=http://localhost:8001
GPU_INTERNAL_TOKEN=dev-internal-token
```
사진 업로드 플로우 돌리면 자동으로 우리 FastAPI 호출됨.

### AI 서버 없이 콜백 핸들러만 테스트하고 싶을 때
새 `/blog-callback` 같은 콜백 엔드포인트 개발 시 굳이 Python 안 띄워도 됨. curl로 가짜 페이로드 POST:
```bash
curl -X POST http://localhost:3000/internal/jobs/test-uuid/blog-callback \
  -H "X-Internal-Token: dev-internal-token" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "테스트 블로그",
    "summary": "요약 한 문단",
    "sections": [
      {"photoIds": ["photo-uuid-1"], "text": "본문 2-4문장"}
    ]
  }'
```

---


## 아키텍처

```
[NestJS]  ──POST /vlm/analyze──→  [FastAPI]
                                     │ (백그라운드: classify + 콜백)
                                     ↓
[NestJS]  ←──POST /internal/jobs/<id>/callback──

[NestJS]  ──POST /blog/generate──→  [FastAPI]
                                       │ (백그라운드: dedup + LLM + 콜백)
                                       ↓
[NestJS]  ←──POST /internal/jobs/<id>/blog-callback──
```

## 인증

모든 요청·콜백에 헤더:
```
X-Internal-Token: <GPU_INTERNAL_TOKEN env var>
```

NestJS와 FastAPI가 같은 `GPU_INTERNAL_TOKEN` 값을 공유해야 함.

---

## 1. 사진 분류 (`/vlm/analyze`)

### 백엔드 상태: ✅ 완전 호환, 추가 작업 없음

기존 NestJS의 `WebhookController` + `JobCallbackDto`가 그대로 동작.

### 요청 (NestJS 워커 → FastAPI)
```http
POST /vlm/analyze
X-Internal-Token: <token>
Content-Type: application/json

{
  "job_id": "uuid",
  "photos": [
    { "photo_id": "uuid", "url": "<S3 presigned GET URL>" }
  ],
  "callback_url": "https://<nestjs>/internal/jobs/<job_id>/callback"
}
```

### 응답
`202 Accepted` 즉시 반환. 실제 처리는 백그라운드.

### 콜백 (FastAPI → NestJS)
```http
POST <callback_url>
X-Internal-Token: <같은 token>

{
  "results": [
    {
      "photoId": "uuid",
      "sceneLabel": "person" | "landscape" | "food" | "architecture" | "cityscape",
      "aiCaption": "",
      "aiKeywords": []
    }
  ]
}
```

### 메모
- `aiCaption`과 `aiKeywords`는 현재 항상 빈 값. NestJS DTO가 빈 문자열·빈 배열 허용하므로 그대로 통과.
- 나중에 캡션·키워드 모델 추가하면 [server/tasks.py](server/tasks.py)의 `process_vlm_analyze`에서 채우면 됨. 백엔드 변경 불필요.

---

## 2. 블로그 생성 (`/blog/generate`)

### 백엔드 상태: ⚠️ 백엔드 작업 필요

NestJS 측에 추가해야 할 것:

- [ ] **트리거 엔드포인트**: `POST /blogs/:roomId/generate`
  - `BlogsService.generateFromRoom(roomId, options)` 추가
  - `ProcessingJob(LLM_BLOG_DRAFT)` 생성 + BullMQ 적재
  - 즉시 `{ jobId, status: "PENDING" }` 반환
- [ ] **BullMQ 워커**: 블로그 잡 처리
  - 기존 `GpuJobsProcessor`에 job name 분기 추가 또는 별도 프로세서
  - 사진 목록 조회 → presigned URL 생성 → FastAPI `/blog/generate` 호출
- [ ] **콜백 DTO**: `BlogCallbackDto` — 아래 형식
- [ ] **콜백 컨트롤러**: `WebhookController`에 `@Post(':jobId/blog-callback')` 메서드
- [ ] **콜백 처리 로직**:
  1. `dto.sections[].photoIds`가 모두 job의 room에 속하는지 검증
  2. `Blog` row 생성 (title, content는 sections JSON 직렬화 또는 마크다운 변환)
  3. `BlogPhoto` row들 생성 (sections 순서대로 `orderIdx`)
  4. `ProcessingJob` SUCCESS 마킹
  5. WebSocket `blog:generated` 이벤트 emit

### 요청 (NestJS 워커 → FastAPI)
```http
POST /blog/generate
X-Internal-Token: <token>
Content-Type: application/json

{
  "job_id": "uuid",
  "photos": [
    {
      "photo_id": "uuid",
      "url": "<S3 presigned GET URL>",
      "taken_at": "2024-02-05T19:40:41" | null,
      "lat": 37.5665 | null,
      "lng": 126.978 | null,
      "scene_label": "food" | null
    }
  ],
  "callback_url": "https://<nestjs>/internal/jobs/<job_id>/blog-callback",
  "persona": "friendly_diary" | "emotional_essay" | "witty" | "concise_log" | "magazine" | null
}
```

- `scene_label`은 선택사항 — 이전에 `/vlm/analyze`로 받은 값을 전달하면 LLM이 더 정확한 글 작성. 없어도 동작.
- `persona`는 선택사항 — 생략 시 `friendly_diary` 사용.

### 응답
`202 Accepted` 즉시 반환. 처리 30~50초 소요 (dedup + LLM).

### 콜백 (FastAPI → NestJS)
```http
POST <callback_url>
X-Internal-Token: <같은 token>

{
  "title": "후쿠오카의 반짝이는 밤",
  "summary": "여행 전체 분위기 한 문단",
  "sections": [
    {
      "photoIds": ["uuid-1", "uuid-2"],
      "text": "이 사진들에 대한 본문 2~4문장"
    },
    {
      "photoIds": ["uuid-3"],
      "text": "다음 섹션 본문"
    }
  ]
}
```

### 메모
- 한 섹션의 `photoIds`는 1~4장.
- 입력 `photos`의 모든 `photo_id`가 어느 섹션엔가 정확히 한 번씩 등장 (FastAPI 측에서 검증).
- `sections` 순서가 곧 사용자에게 보여줄 순서.

---

## 환경변수

### FastAPI 측 (`.env`)
```
GEMINI_API_KEY=<Google AI Studio에서 발급>
LLM_MODEL=gemini-2.0-flash  # 또는 gemini-2.5-flash, gemini-2.5-pro
GPU_INTERNAL_TOKEN=<NestJS와 동일>
```

### NestJS 측 (이미 `src/config/env.schema.ts`에 정의)
```
GPU_SERVER_URL=<FastAPI 배포 URL>
GPU_INTERNAL_TOKEN=<FastAPI와 동일>
CALLBACK_BASE_URL=<NestJS 공개 URL>
JOB_STALL_TIMEOUT_MS=300000
```

---

## 페르소나 (블로그 생성용)

| ID | 설명 |
|---|---|
| `friendly_diary` | 친근한 일기체 (디폴트) |
| `emotional_essay` | 서정적 묘사 중심 |
| `witty` | 재치·과장·자기조롱 |
| `concise_log` | 간결한 메모 |
| `magazine` | 매거진·가이드 톤 |

추가·수정은 [blog/prompt.py](blog/prompt.py)의 `PERSONAS` 딕셔너리.

---

## 재시도·실패 처리

- **Gemini 503/429/5xx**: FastAPI가 exponential backoff로 자동 재시도 (최대 4회, [blog/llm.py](blog/llm.py)).
- **재시도 모두 실패**: FastAPI는 콜백을 보내지 않음 → NestJS의 `StalledJobScheduler`가 `JOB_STALL_TIMEOUT_MS`(기본 5분) 후 FAILED 처리.
- **다운로드·검증 실패**: 로깅만 하고 콜백 안 보냄. 같은 stalled-job 회복 메커니즘이 적용됨.

---

## 보안

- 콜백 URL은 NestJS가 만들어 보내고 FastAPI는 그대로 호출. URL 검증은 NestJS의 `InternalAuthGuard`가 토큰으로 수행.
- 사진 URL은 S3 presigned GET, TTL 1시간 — FastAPI가 만료 전 다운로드해야 함.
- Gemini API 키는 FastAPI 서버에만 존재. 백엔드·앱은 모름.
