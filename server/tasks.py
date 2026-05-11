from __future__ import annotations

import asyncio
import logging
import os

import httpx

from blog import generate_blog_from_photos
from blog.types import PhotoInput
from classifier import classify

from .downloader import download_all
from .schemas import (
    BlogCallback,
    BlogGenerateRequest,
    BlogSectionOut,
    VlmAnalyzeRequest,
    VlmCallback,
    VlmResult,
)

logger = logging.getLogger(__name__)


def _internal_token() -> str:
    return os.environ.get("GPU_INTERNAL_TOKEN", "dev-internal-token")


async def process_vlm_analyze(req: VlmAnalyzeRequest) -> None:
    try:
        urls = [p.url for p in req.photos]
        photo_bytes = await download_all(urls)

        results: list[VlmResult] = []
        for photo, image_bytes in zip(req.photos, photo_bytes):
            label = await asyncio.to_thread(classify, image_bytes)
            results.append(
                VlmResult(
                    photoId=photo.photo_id,
                    sceneLabel=label,
                    aiCaption="",
                    aiKeywords=[],
                )
            )

        callback = VlmCallback(results=results)
        await _send_callback(req.callback_url, callback.model_dump())
    except Exception:
        logger.exception("VLM analyze failed for job %s", req.job_id)


async def process_blog_generate(req: BlogGenerateRequest) -> None:
    try:
        urls = [p.url for p in req.photos]
        photo_bytes = await download_all(urls)

        photos: list[PhotoInput] = []
        for photo, image_bytes in zip(req.photos, photo_bytes):
            photos.append(
                PhotoInput(
                    photo_id=photo.photo_id,
                    image_bytes=image_bytes,
                    taken_at=photo.taken_at,
                    lat=photo.lat,
                    lng=photo.lng,
                    scene_label=photo.scene_label,
                )
            )

        kwargs = {"persona": req.persona} if req.persona else {}
        draft = await asyncio.to_thread(
            generate_blog_from_photos, photos, **kwargs
        )

        callback = BlogCallback(
            title=draft["title"],
            summary=draft["summary"],
            sections=[
                BlogSectionOut(photoIds=s["photo_ids"], text=s["text"])
                for s in draft["sections"]
            ],
        )
        await _send_callback(req.callback_url, callback.model_dump())
    except Exception:
        logger.exception("Blog generate failed for job %s", req.job_id)


async def _send_callback(url: str, payload: dict) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json=payload,
            headers={"X-Internal-Token": _internal_token()},
            timeout=30.0,
        )
        response.raise_for_status()
