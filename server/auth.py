from __future__ import annotations

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.config import AGENTHQ_TOKEN

_bearer_scheme = HTTPBearer()


async def require_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """Dependency that validates the Bearer token on HTTP endpoints."""
    if not AGENTHQ_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AGENTHQ_TOKEN is not configured on the server",
        )
    if credentials.credentials != AGENTHQ_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
        )
    return credentials.credentials


async def require_ws_token(token: str = Query(...)) -> str:
    """Dependency-style helper for WebSocket token validation via query param."""
    if not AGENTHQ_TOKEN:
        raise ValueError("AGENTHQ_TOKEN is not configured on the server")
    if token != AGENTHQ_TOKEN:
        raise ValueError("Invalid token")
    return token
