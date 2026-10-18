"""The parts' APIs accept only requests carrying the shared service token."""

import secrets

from fastapi import Header, HTTPException

from my_browser.common.settings import service_token


def require_token(authorization: str = Header(default="")) -> None:
    if not secrets.compare_digest(authorization.encode(), f"Bearer {service_token()}".encode()):
        raise HTTPException(status_code=401, detail="service token required")
