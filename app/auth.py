"""Authentication middleware and session management.

Simple cookie-based session authentication for the admin dashboard.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from fastapi import Request, Response
from fastapi.responses import RedirectResponse

# In-memory session store (sufficient for single-instance deployment)
_SESSIONS: dict[str, dict[str, Any]] = {}

SESSION_COOKIE = "admin_session"
SESSION_MAX_AGE = 86400  # 24 hours


def create_session(username: str) -> str:
    """Create a new session and return the session token."""
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {
        "username": username,
        "created_at": time.time(),
    }
    return token


def verify_session(request: Request) -> dict[str, Any] | None:
    """Verify a session cookie. Returns session data or None."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or token not in _SESSIONS:
        return None
    session = _SESSIONS[token]
    # Check expiry
    if time.time() - session["created_at"] > SESSION_MAX_AGE:
        del _SESSIONS[token]
        return None
    return session


def destroy_session(request: Request, response: Response) -> None:
    """Destroy the current session."""
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in _SESSIONS:
        del _SESSIONS[token]
    response.delete_cookie(SESSION_COOKIE)


def set_session_cookie(response: Response, token: str) -> None:
    """Set the session cookie on a response."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def require_auth(request: Request) -> RedirectResponse | None:
    """Check if user is authenticated. Returns redirect if not."""
    session = verify_session(request)
    if session is None:
        return RedirectResponse(url="/admin/login", status_code=302)
    return None
