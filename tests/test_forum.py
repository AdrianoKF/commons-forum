import asyncio
import re
import threading
import time

import httpx
import pytest
from conftest import csrf, sign_in, submit
from sqlalchemy import select

import forum.routes as routes
from forum.cli import initialize, reset
from forum.config import Settings
from forum.models import Attachment, Membership, Post, Reply, User
from forum.security import AuthAttemptLimiter, AuthWorkLimiter, AuthWorkLimitExceeded


def test_public_home_and_board(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "The Commons" in client.get("/").text
    assert "A place to compare notes" in client.get("/boards/1").text
    assert "A bookshelf" in client.get("/posts/4").text
    assert client.get("/static/style.css").status_code == 200


def test_registration_login_logout(client, app):
    response = submit(client, "/register", username="charlie", password="a-new-demo-password")
    assert response.status_code == 303
    assert "charlie" in client.get("/").text
    with app.state.session_factory() as db:
        user = db.scalar(select(User).where(User.username == "charlie"))
        assert user.role == "member"
        assert not db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    assert submit(client, "/logout").status_code == 303
    assert "Sign in" in client.get("/").text
    assert submit(client, "/login", username="charlie", password="wrong").status_code == 401
    assert (
        submit(client, "/login", username="charlie", password="a-new-demo-password").status_code
        == 303
    )


def test_registration_validation(client):
    assert submit(client, "/register", username="bad user", password="short").status_code == 422
    assert (
        submit(client, "/register", username="alice", password="another-password").status_code
        == 409
    )


def test_duplicate_registration_skips_hash_and_is_throttled(client, app, monkeypatch):
    app.state.auth_attempt_limiter = AuthAttemptLimiter(source_limit=10, account_limit=1)

    def unexpected_hash(_password):
        raise AssertionError("duplicate registrations must not hash passwords")

    monkeypatch.setattr(routes, "hash_password", unexpected_hash)
    first = submit(client, "/register", username="alice", password="another-password")
    second = submit(client, "/register", username="alice", password="another-password")
    assert first.status_code == 409
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"


def test_auth_attempt_limits_are_uniform_and_expire(client, app, monkeypatch):
    now = [0.0]
    app.state.auth_attempt_limiter = AuthAttemptLimiter(
        window_seconds=60,
        source_limit=10,
        account_limit=1,
        clock=lambda: now[0],
    )
    monkeypatch.setattr(routes, "verify_password", lambda _password, _encoded: False)

    assert submit(client, "/login", username="alice", password="wrong").status_code == 401
    known_limited = submit(client, "/login", username="alice", password="wrong")
    assert submit(client, "/login", username="unknown", password="wrong").status_code == 401
    unknown_limited = submit(client, "/login", username="unknown", password="wrong")
    assert known_limited.status_code == unknown_limited.status_code == 429
    assert known_limited.text == unknown_limited.text
    assert known_limited.headers["retry-after"] == "60"

    now[0] = 61.0
    assert submit(client, "/login", username="alice", password="wrong").status_code == 401

    app.state.auth_attempt_limiter = AuthAttemptLimiter(source_limit=2, account_limit=10)
    assert submit(client, "/login", username="one", password="wrong").status_code == 401
    duplicate = submit(client, "/register", username="alice", password="another-password")
    assert duplicate.status_code == 409
    source_limited = submit(client, "/login", username="two", password="wrong")
    assert source_limited.status_code == 429


def test_auth_attempt_key_cap_does_not_evict_active_limits():
    now = [0.0]
    limiter = AuthAttemptLimiter(
        window_seconds=60,
        source_limit=1,
        account_limit=1,
        max_keys=2,
        clock=lambda: now[0],
    )
    assert limiter.admit("source-1", "login", "victim") is None
    assert limiter.admit("source-2", "login", "other") is None
    assert limiter.admit("source-3", "login", "third") == 60
    assert limiter.admit("source-1", "login", "victim") == 60
    now[0] = 61.0
    assert limiter.admit("source-3", "login", "third") is None


def test_auth_work_is_offloaded_and_globally_bounded(app, monkeypatch):
    app.state.auth_attempt_limiter = AuthAttemptLimiter(source_limit=100, account_limit=100)
    app.state.auth_work_limiter = AuthWorkLimiter(capacity=1)
    started = threading.Event()
    release = threading.Event()

    def slow_verify(_password, _encoded):
        started.set()
        if not release.wait(timeout=2):
            raise AssertionError("test did not release password worker")
        return False

    monkeypatch.setattr(routes, "verify_password", slow_verify)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as browser:
            page = await browser.get("/login")
            token = re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)
            started_at = time.monotonic()
            login = asyncio.create_task(
                browser.post(
                    "/login",
                    data={"_csrf": token, "username": "alice", "password": "wrong"},
                )
            )
            try:
                await asyncio.sleep(0.05)
                assert started.is_set()
                assert time.monotonic() - started_at < 0.3
                assert (await browser.get("/health")).status_code == 200
                busy = await browser.post(
                    "/register",
                    data={
                        "_csrf": token,
                        "username": "charlie",
                        "password": "a-new-demo-password",
                    },
                )
                assert busy.status_code == 503
                assert busy.headers["retry-after"] == "1"
            finally:
                release.set()
            assert (await login).status_code == 401

    asyncio.run(exercise())


def test_cancelled_auth_work_holds_capacity_until_worker_finishes():
    limiter = AuthWorkLimiter(capacity=1)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_work():
        started.set()
        release.wait(timeout=2)
        finished.set()

    async def exercise():
        first = asyncio.create_task(limiter.run(blocking_work))
        await asyncio.to_thread(started.wait, 1)
        first.cancel()
        await asyncio.sleep(0)
        with pytest.raises(AuthWorkLimitExceeded):
            await limiter.run(lambda: None)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.to_thread(finished.wait, 1)
        while limiter._active:
            await asyncio.sleep(0)
        assert await limiter.run(lambda: "finished") == "finished"

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        limiter.close()


def test_create_edit_reply_and_quote(client, app):
    sign_in(client)
    response = submit(client, "/boards/1/posts", title="Weekend plans", body="A walk by the river.")
    path = response.headers["location"]
    assert response.status_code == 303
    assert "A walk by the river." in client.get(path).text
    assert client.get(path + "/edit").status_code == 200
    assert (
        submit(client, path + "/edit", title="Sunday plans", body="Bring a picnic.").status_code
        == 303
    )
    reply_response = submit(client, path + "/replies", body="I’ll be there.", quote_post="1")
    assert reply_response.status_code == 303
    html = client.get(path).text
    assert "<blockquote>" in html and "I’ll be there." in html
    with app.state.session_factory() as db:
        reply = db.scalar(select(Reply).order_by(Reply.id.desc()))
        reply_id = reply.id
    assert client.get(f"/replies/{reply_id}/edit").status_code == 200
    assert submit(client, f"/replies/{reply_id}/edit", body="At noon!").status_code == 303
    assert "At noon!" in client.get(path).text


def test_upload_preview_download_delete(client, app):
    sign_in(client)
    response = client.post(
        "/posts/4/attachments",
        data={"_csrf": csrf(client)},
        files={"file": ("notes.txt", b"Some useful notes.", "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as db:
        attachment = db.scalar(select(Attachment).order_by(Attachment.id.desc()))
        ident, name = attachment.id, attachment.storage_name
    assert "Some useful notes." in client.get(f"/attachments/{ident}/preview").text
    response = client.get(f"/attachments/{ident}/download")
    assert response.content == b"Some useful notes."
    assert response.headers["content-disposition"].startswith("attachment;")
    assert submit(client, f"/attachments/{ident}/delete").status_code == 303
    assert not (app.state.settings.media_root / name).exists()
    assert client.get(f"/attachments/{ident}/download").status_code == 404


def test_search(client):
    sign_in(client)
    assert "Saturday planting plan" in client.get("/search", params={"q": "planting"}).text
    assert "No conversations matched" in client.get("/search", params={"q": "no-such-topic"}).text


def test_moderator_workflow(client, app):
    sign_in(client, "morgan")
    assert (
        submit(client, "/posts/4/moderate", pinned="true", locked="true", hidden="true").status_code
        == 303
    )
    with app.state.session_factory() as db:
        post = db.get(Post, 4)
        assert post.pinned and post.locked and post.hidden
    assert "Hidden" in client.get("/posts/4").text
    assert submit(client, "/posts/4/moderate", hidden="false", locked="false").status_code == 303


def test_admin_membership_workflow(client, app):
    sign_in(client, "admin")
    assert client.get("/admin").status_code == 200
    for action in ("grant", "revoke"):
        assert (
            submit(
                client, "/admin/memberships", user_id="2", board_id="2", action=action
            ).status_code
            == 303
        )
        with app.state.session_factory() as db:
            assert (db.get(Membership, (2, 2)) is not None) == (action == "grant")
    assert "revoke board membership" in client.get("/admin").text


def test_reset_restores_fixture_content(tmp_path):
    settings = Settings(tmp_path / "reset-data")
    initialize(settings)
    before = {p.name: p.read_bytes() for p in settings.media_root.iterdir()}
    (settings.media_root / "extra").write_text("temporary")
    (settings.data_dir / "keep.txt").write_text("unrelated")
    reset(settings)
    after = {p.name: p.read_bytes() for p in settings.media_root.iterdir()}
    assert before == after
    assert (settings.data_dir / "keep.txt").read_text() == "unrelated"
