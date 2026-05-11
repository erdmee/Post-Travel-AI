from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


def verify_internal_token(x_internal_token: str = Header(...)) -> None:
    expected = os.environ.get("GPU_INTERNAL_TOKEN", "dev-internal-token")
    if x_internal_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )
