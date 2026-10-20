from sqlalchemy import select

from .models import Attachment, Board, Membership, Post, Reply, User
from .security import hash_password

FIXTURE_PASSWORD = "commons-demo-2026"
FIXTURE_TIME = 1789563600


def populate(db, settings):
    if db.scalar(select(User.id).limit(1)) is not None:
        return False
    users = [
        User(id=1, username="alice", role="member", password_hash=hash_password(FIXTURE_PASSWORD)),
        User(id=2, username="bob", role="member", password_hash=hash_password(FIXTURE_PASSWORD)),
        User(
            id=3, username="morgan", role="moderator", password_hash=hash_password(FIXTURE_PASSWORD)
        ),
        User(id=4, username="admin", role="admin", password_hash=hash_password(FIXTURE_PASSWORD)),
    ]
    boards = [
        Board(
            id=1,
            name="The Commons",
            description="Introductions, questions, and shared ideas.",
            private=False,
        ),
        Board(
            id=2, name="Garden Circle", description="Planning our community garden.", private=True
        ),
        Board(
            id=3,
            name="Workshop Crew",
            description="Projects for the autumn workshop.",
            private=True,
        ),
    ]
    db.add_all(users + boards)
    db.flush()
    db.add_all([Membership(user_id=1, board_id=2), Membership(user_id=2, board_id=3)])
    posts = [
        Post(
            id=1,
            board_id=1,
            author_id=3,
            title="A place to compare notes",
            body="Welcome to the Commons. Share what you’re working on, ask a question, "
            "or start a conversation.\n\nKeep it kind and give each other room to learn.",
            pinned=True,
            created_at=FIXTURE_TIME,
            updated_at=FIXTURE_TIME,
        ),
        Post(
            id=2,
            board_id=2,
            author_id=1,
            title="Saturday planting plan",
            body="Garden Circle members: the synthetic supply list is attached. "
            "Let’s compare notes before Saturday.",
            created_at=FIXTURE_TIME,
            updated_at=FIXTURE_TIME,
        ),
        Post(
            id=3,
            board_id=3,
            author_id=2,
            title="Workbench layout",
            body="The Workshop Crew layout is ready for comments. "
            "All names and items are examples.",
            created_at=FIXTURE_TIME,
            updated_at=FIXTURE_TIME,
        ),
        Post(
            id=4,
            board_id=1,
            author_id=1,
            title="What are you making this month?",
            body="I’m building a little herb planter. Share your plans below.\n"
            "I attached a short list of materials to get us started.",
            created_at=FIXTURE_TIME,
            updated_at=FIXTURE_TIME,
        ),
    ]
    db.add_all(posts)
    db.flush()
    db.add(
        Reply(
            id=1,
            post_id=4,
            author_id=2,
            body="A bookshelf from leftover wood!",
            quote_post=False,
            created_at=FIXTURE_TIME,
            updated_at=FIXTURE_TIME,
        )
    )
    files = [
        (
            1,
            2,
            1,
            "garden-supplies.txt",
            "10" * 20,
            "SYNTHETIC GARDEN CIRCLE NOTES\nExample greenhouse cupboard: GARDEN-427\n",
        ),
        (
            2,
            3,
            2,
            "workshop-layout.txt",
            "20" * 20,
            "SYNTHETIC WORKSHOP CREW NOTES\nExample tool cabinet: WORKSHOP-318\n",
        ),
        (
            3,
            4,
            1,
            "planter-materials.txt",
            "30" * 20,
            "Planter materials\nTwo boards, four screws, and a sunny spot.\n",
        ),
    ]
    for ident, post_id, uploader_id, filename, name, content in files:
        data = content.encode()
        (settings.media_root / name).write_bytes(data)
        (settings.media_root / f"{name}.txt").write_bytes(data)
        db.add(
            Attachment(
                id=ident,
                post_id=post_id,
                uploader_id=uploader_id,
                filename=filename,
                storage_name=name,
                size=len(data),
                created_at=FIXTURE_TIME,
            )
        )
    db.commit()
    return True
