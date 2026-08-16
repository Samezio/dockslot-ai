"""Admin session auth for the /admin portal (dispatcher-facing, not a
driver channel).

Deliberately minimal: one shared admin login (ADMIN_USERNAME /
ADMIN_PASSWORD env vars, see .env.example), an in-memory session store, and
FastAPI dependencies that routes use to require a valid session. No user
table, no roles, no password hashing beyond secrets.compare_digest -- this
matches the rest of the project's current no-real-auth posture (see
app/api.py's module docstring) while still gating the admin views behind a
login. Sessions live only in process memory: a server restart logs
everyone out, which is fine for an admin dashboard and not something worth
adding a table for.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from fastapi import Cookie, HTTPException

SESSION_COOKIE_NAME = "dockslot_admin_session"
SESSION_TTL = timedelta(hours=8)

# session_token -> expiry (UTC). Fine to keep in a module-level dict: this
# is a single-process dev tool, same assumption app/db.py's connection
# handling already makes.
_sessions: Dict[str, datetime] = {}


def _admin_credentials() -> Tuple[str, str]:
    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "ADMIN_USERNAME / ADMIN_PASSWORD are not set. Add them to .env "
            "(see .env.example) before using the admin portal."
        )
    return username, password


def verify_admin_login(username: str, password: str) -> bool:
    expected_username, expected_password = _admin_credentials()
    # compare_digest on both fields (not `and`-shortcut on a dict lookup)
    # so a wrong username doesn't take a measurably different path than a
    # wrong password.
    username_ok = secrets.compare_digest(username, expected_username)
    password_ok = secrets.compare_digest(password, expected_password)
    return username_ok and password_ok


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = datetime.now(timezone.utc) + SESSION_TTL
    return token


def destroy_session(token: Optional[str]) -> None:
    if token:
        _sessions.pop(token, None)


def _session_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    expiry = _sessions.get(token)
    if expiry is None:
        return False
    if datetime.now(timezone.utc) >= expiry:
        _sessions.pop(token, None)
        return False
    return True


def is_authenticated(session_token: Optional[str]) -> bool:
    """For page routes (GET /admin) that want to redirect to the login
    page rather than return the JSON api's plain 401."""
    return _session_valid(session_token)


def require_admin_session(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> None:
    """FastAPI dependency for /admin/api/* routes -- these are called by
    the dashboard's own JS, so a 401 (not a redirect) is the right
    response to an expired or missing session."""
    if not _session_valid(session_token):
        raise HTTPException(status_code=401, detail="Admin session expired or missing. Please log in again.")
