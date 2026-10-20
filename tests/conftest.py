import re

import pytest
from fastapi.testclient import TestClient

from forum.config import Settings
from forum.fixtures import FIXTURE_PASSWORD, populate
from forum.main import create_app


def csrf(client, path="/"):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="_csrf" value="([^"]+)"', response.text)
    if match is None:
        response = client.get("/login")
        match = re.search(r'name="_csrf" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def sign_in(client, username="alice"):
    response = client.post(
        "/login",
        data={"_csrf": csrf(client, "/login"), "username": username, "password": FIXTURE_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303


def submit(client, path, **data):
    return client.post(path, data={"_csrf": csrf(client), **data}, follow_redirects=False)


@pytest.fixture
def app(tmp_path):
    application = create_app(Settings(tmp_path / "data"))
    with application.state.session_factory() as db:
        populate(db, application.state.settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as browser:
        yield browser
