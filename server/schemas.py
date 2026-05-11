from __future__ import annotations

from pydantic import BaseModel


class VlmPhotoItem(BaseModel):
    photo_id: str
    url: str


class VlmAnalyzeRequest(BaseModel):
    job_id: str
    photos: list[VlmPhotoItem]
    callback_url: str


class BlogPhotoItem(BaseModel):
    photo_id: str
    url: str
    taken_at: str | None = None
    lat: float | None = None
    lng: float | None = None
    scene_label: str | None = None


class BlogGenerateRequest(BaseModel):
    job_id: str
    photos: list[BlogPhotoItem]
    callback_url: str
    persona: str | None = None


# Callback payloads — wire format that NestJS expects (camelCase to match its DTOs).


class VlmResult(BaseModel):
    photoId: str
    sceneLabel: str
    aiCaption: str
    aiKeywords: list[str]


class VlmCallback(BaseModel):
    results: list[VlmResult]


class BlogSectionOut(BaseModel):
    photoIds: list[str]
    text: str


class BlogCallback(BaseModel):
    title: str
    summary: str
    sections: list[BlogSectionOut]
