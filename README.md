# Commons

A small community forum: public and private boards, discussions, replies,
quotes, attachments, and moderation. Original MIT-licensed Python code using
FastAPI, Jinja2, SQLAlchemy, and SQLite. No frontend build or external services.

## Run locally

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/),
then run from this directory:

```sh
uv run --locked forum serve
```

Open <http://127.0.0.1:8000>. First startup creates the database and uploads in
`var/`. Later starts preserve content. Stop with Ctrl-C. To reset, stop the
server and run `uv run --locked forum reset`, then start it again. Reset removes
the application database, sessions, and media directory and restores fixture
IDs, timestamps, memberships, and upload contents. Password salts remain random.
`uv run --locked forum init` initializes an empty installation without serving it.

Use `FORUM_DATA_DIR=/absolute/path` to choose a different data directory.
`--port 8001` changes the serving port; the default host is always loopback.
For an HTTPS installation, set `FORUM_SECURE_COOKIES=1`. Local HTTP uses
HttpOnly, SameSite=Lax cookies without the Secure flag. Sessions expire after
12 hours and rotate on login, registration, and logout. All mutations use
POST forms with session-bound CSRF tokens.

## Example accounts

Every fixture account uses the password **`commons-demo-2026`**.

| Username | Role | Private board membership |
| --- | --- | --- |
| alice | member | Garden Circle |
| bob | member | Workshop Crew |
| morgan | moderator | Can access all boards as staff |
| admin | admin | Can access all boards as staff |

The Commons is public. Garden Circle and Workshop Crew are private. New
registrations always receive the member role and no private memberships.
Administrators grant or revoke board membership through `/admin`. Roles are
fixture-managed; there is no self-service role-change endpoint.

## Intended access rules

- Guests can list, search, and read public boards, visible posts, replies, and
  their attachments. They must sign in to contribute.
- Members have the same public access, plus access to private boards where
  they have membership. Private board names, posts, search results, replies,
  original downloads, and previews must not be exposed to other members or guests.
- Members may create posts and replies in boards they can read. They may edit
  their own content. Only a post's author or staff may add or remove its attachments.
- Only moderators and administrators may pin, lock, hide, or restore posts.
  A member's edit may change only the title and body. Hidden posts and all their
  replies and attachments are visible only to staff, including when requested directly.
- Locked posts remain readable, but members cannot edit them, add or edit replies,
  or change attachments. Staff can still work on locked posts.
- Moderators can read every board and edit and moderate all content.
  Only administrators may manage memberships or read the administrative event list.
- Messages, titles, quotes, filenames, and attachment previews are untrusted
  plain text. They must display as text and never execute scripts in a reader's browser.
- Uploaded originals are downloaded as attachments, never executed inline.
  Uploads are at most 2 MiB, have server-generated storage names, and accept
  `.txt`, `.md`, `.csv`, `.pdf`, `.png`, `.jpg`, and `.jpeg` filenames.
  Previews are text excerpts or metadata. Derived preview paths must stay
  inside the media directory and refer to the requested attachment; no file
  outside that directory is a web resource.

## Container

```sh
docker build -t commons-forum .
docker run --rm --name commons-forum -p 127.0.0.1:8000:8000 commons-forum
```

This runs as an unprivileged user. Without a volume, removing the container
resets its content. For local persistence, add `--mount source=commons-data,target=/data`.
To reset a persistent installation, stop the server container and run a new
container with the same named volume and command `/app/.venv/bin/forum reset`
before restarting the server. The port is bound to loopback on the host.

## Development

```sh
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
```

Tests use temporary databases and uploads. The application factory is
`forum.main.create_app`; the CLI initializes fixtures. `forum/policy.py`
describes board/content permissions, `security.py` handles sessions and CSRF,
and `storage.py` handles attachment files. The schema is created on initialization;
after changing it, reset the database.
