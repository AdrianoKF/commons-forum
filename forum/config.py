import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    secure_cookies: bool = False
    session_hours: int = 12
    max_upload_bytes: int = 2 * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.data_dir / "forum.sqlite3"

    @property
    def media_root(self) -> Path:
        return self.data_dir / "media"

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.environ.get("FORUM_DATA_DIR", "var")).resolve(),
            secure_cookies=os.environ.get("FORUM_SECURE_COOKIES", "0") == "1",
        )
