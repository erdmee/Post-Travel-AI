from __future__ import annotations

import asyncio

import httpx


async def _download_one(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.content


async def download_all(urls: list[str]) -> list[bytes]:
    async with httpx.AsyncClient() as client:
        tasks = [_download_one(client, url) for url in urls]
        return await asyncio.gather(*tasks)
