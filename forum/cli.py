import argparse
import shutil

import uvicorn

from .config import Settings
from .db import make_database
from .fixtures import populate


def initialize(settings):
    engine, factory = make_database(settings)
    try:
        with factory() as db:
            populate(db, settings)
    finally:
        engine.dispose()


def reset(settings):
    # Only application-owned paths are removed. Stop the server before resetting.
    for suffix in ("", "-wal", "-shm"):
        settings.database_path.with_name(settings.database_path.name + suffix).unlink(
            missing_ok=True
        )
    if settings.media_root.is_symlink():
        settings.media_root.unlink()
    elif settings.media_root.exists():
        shutil.rmtree(settings.media_root)
    initialize(settings)


def main():
    parser = argparse.ArgumentParser(description="Run the Commons forum")
    parser.add_argument("command", choices=["serve", "reset", "init"], nargs="?", default="serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "reset":
        reset(settings)
        print(f"Reset forum database and uploads in {settings.data_dir}")
    elif args.command == "init":
        initialize(settings)
        print(f"Initialized {settings.data_dir}")
    else:
        initialize(settings)
        uvicorn.run("forum.main:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
