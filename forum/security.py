import asyncio
import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete

from .models import LoginSession, User

COOKIE_NAME = "commons_session"


class AuthWorkLimitExceeded(RuntimeError):
    pass


class AuthAttemptLimiter:
    """Bound accepted authentication attempts without retaining unlimited keys."""

    def __init__(
        self,
        window_seconds=60,
        source_limit=20,
        account_limit=10,
        max_keys=10_000,
        clock=time.monotonic,
    ):
        if min(window_seconds, source_limit, account_limit, max_keys) <= 0:
            raise ValueError("Authentication limits must be positive.")
        self.window_seconds = window_seconds
        self.source_limit = source_limit
        self.account_limit = account_limit
        self.max_keys = max_keys
        self.clock = clock
        self._source_attempts = OrderedDict()
        self._account_attempts = OrderedDict()
        self._lock = threading.Lock()

    def _active(self, table, key, now):
        attempts = table.get(key)
        if attempts is None:
            return None
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del table[key]
            return None
        return attempts

    def _discard_expired(self, table, now):
        cutoff = now - self.window_seconds
        while table:
            attempts = next(iter(table.values()))
            if attempts[-1] > cutoff:
                break
            table.popitem(last=False)

    def _record(self, table, key, now):
        attempts = table.get(key)
        if attempts is None:
            attempts = deque()
            table[key] = attempts
        attempts.append(now)
        table.move_to_end(key)

    def admit(self, source, operation, account):
        now = self.clock()
        account_key = (operation, account)
        with self._lock:
            source_attempts = self._active(self._source_attempts, source, now)
            account_attempts = self._active(self._account_attempts, account_key, now)
            self._discard_expired(self._source_attempts, now)
            self._discard_expired(self._account_attempts, now)
            blocked = []
            if source_attempts and len(source_attempts) >= self.source_limit:
                blocked.append(source_attempts[0])
            if account_attempts and len(account_attempts) >= self.account_limit:
                blocked.append(account_attempts[0])
            if source_attempts is None and len(self._source_attempts) >= self.max_keys:
                blocked.append(next(iter(self._source_attempts.values()))[-1])
            if account_attempts is None and len(self._account_attempts) >= self.max_keys:
                blocked.append(next(iter(self._account_attempts.values()))[-1])
            if blocked:
                remaining = max(self.window_seconds - (now - oldest) for oldest in blocked)
                return max(1, math.ceil(remaining))
            self._record(self._source_attempts, source, now)
            self._record(self._account_attempts, account_key, now)
        return None


class AuthWorkLimiter:
    """Reject excess password work before submitting it to a worker thread."""

    def __init__(self, capacity=2):
        if capacity <= 0:
            raise ValueError("Authentication work capacity must be positive.")
        self.capacity = capacity
        self._active = 0
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=capacity, thread_name_prefix="forum-auth"
        )
        self._closed = False

    def _finished(self, _future):
        with self._lock:
            self._active -= 1

    async def run(self, operation, *args):
        with self._lock:
            if self._closed:
                raise RuntimeError("Authentication work limiter is closed.")
            if self._active >= self.capacity:
                raise AuthWorkLimitExceeded
            self._active += 1
        try:
            worker = self._executor.submit(operation, *args)
        except BaseException:
            with self._lock:
                self._active -= 1
            raise
        worker.add_done_callback(self._finished)
        return await asyncio.wrap_future(worker)

    def close(self):
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True)


def check_auth_attempt(request: Request, operation: str, account: str):
    source = request.client.host if request.client else "unknown"
    retry_after = request.app.state.auth_attempt_limiter.admit(source, operation, account)
    if retry_after is not None:
        raise HTTPException(
            429,
            "Too many authentication attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


async def run_auth_work(request: Request, operation, *args):
    try:
        return await request.app.state.auth_work_limiter.run(operation, *args)
    except AuthWorkLimitExceeded:
        raise HTTPException(
            503,
            "Authentication is temporarily busy. Try again.",
            headers={"Retry-After": "1"},
        ) from None


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
