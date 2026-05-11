from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI

from classifier.classify import _load

from .auth import verify_internal_token
from .schemas import BlogGenerateRequest, VlmAnalyzeRequest
from .tasks import process_blog_generate, process_vlm_analyze

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load()
    yield


app = FastAPI(title="Post-Travel-AI", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/vlm/analyze",
    status_code=202,
    dependencies=[Depends(verify_internal_token)],
)
async def vlm_analyze(req: VlmAnalyzeRequest, tasks: BackgroundTasks) -> dict:
    tasks.add_task(process_vlm_analyze, req)
    return {"job_id": req.job_id, "status": "accepted"}


@app.post(
    "/blog/generate",
    status_code=202,
    dependencies=[Depends(verify_internal_token)],
)
async def blog_generate(
    req: BlogGenerateRequest, tasks: BackgroundTasks
) -> dict:
    tasks.add_task(process_blog_generate, req)
    return {"job_id": req.job_id, "status": "accepted"}
