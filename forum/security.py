import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete

from .models import LoginSession, User

COOKIE_NAME = "commons_session"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${salt}${key.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def database(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def new_session(db, request, user_id=None):
    token = secrets.token_urlsafe(32)
    session = LoginSession(
        token_hash=token_digest(token),
        user_id=user_id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=int(time.time()) + request.app.state.settings.session_hours * 3600,
    )
    db.add(session)
    db.commit()
    request.state.session_cookie = token
    return session


def identity(request: Request, db=Depends(database)) -> LoginSession:
    token = request.cookies.get(COOKIE_NAME, "")
    session = db.get(LoginSession, token_digest(token)) if token else None
    now = int(time.time())
    if session and session.expires_at > now:
        return session
    db.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
    return new_session(db, request)


def rotate_session(db, request, previous, user_id=None):
    db.delete(previous)
    return new_session(db, request, user_id)


def current_user(session=Depends(identity), db=Depends(database)) -> User | None:
    return db.get(User, session.user_id) if session.user_id else None


def require_user(user=Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(401, "Please sign in to continue.")
    return user


async def check_csrf(request: Request, session=Depends(identity)):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        form = await request.form(max_files=1, max_fields=20, max_part_size=2 * 1024 * 1024)
        supplied = form.get("_csrf", "")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, session.csrf_token):
            raise HTTPException(403, "The form expired. Reload the page and try again.")
