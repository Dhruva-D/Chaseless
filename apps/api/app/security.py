from __future__ import annotations

import hmac

from chaseless.core.settings import get_settings
from fastapi import Header, HTTPException, status


def require_internal_token(
    x_internal_token: str = Header(default="", alias="X-Internal-Token"),
) -> None:
    expected = get_settings().internal_service_token
    if not expected or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid internal service token required",
        )
