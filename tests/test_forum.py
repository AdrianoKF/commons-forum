from conftest import csrf, sign_in, submit
from sqlalchemy import select

from forum.cli import initialize, reset
from forum.config import Settings
from forum.models import Attachment, Membership, Post, Reply, User


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
