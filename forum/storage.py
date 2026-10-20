import re
import secrets
import time
from pathlib import Path

from fastapi import HTTPException

from .models import Attachment

ALLOWED_SUFFIXES = {".txt", ".md", ".csv", ".pdf", ".png", ".jpg", ".jpeg"}


def media_path(root: Path, name: str) -> Path:
    root = root.resolve()
    try:
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root) or candidate == root:
            raise HTTPException(404, "File not found.")
        return candidate
    except (ValueError, OSError):
        raise HTTPException(404, "File not found.")


def original_path(settings, attachment):
    path = media_path(settings.media_root, attachment.storage_name)
    if not path.is_file():
        raise HTTPException(404, "File not found.")
    return path


def derived_path(root: Path, name: str) -> Path:
    root = root.resolve()
    try:
        candidate = (root / name).resolve()
        if not str(candidate).startswith(str(root)):
            raise HTTPException(404, "File not found.")
        return candidate
    except (ValueError, OSError):
        raise HTTPException(404, "File not found.")


def preview_path(settings, attachment, variant):
    name = f"{attachment.storage_name}.txt" if not variant else variant
    path = derived_path(settings.media_root, name)
    if not path.is_file() or path.suffix != ".txt":
        raise HTTPException(404, "Preview not found.")
    # A derivative belongs to the attachment identified in the URL.
    if path.name != f"{attachment.storage_name}.txt":
        raise HTTPException(404, "Preview not found.")
    return path


def store_attachment(db, settings, post, user, upload):
    filename = re.sub(
        r"[^\w. ()-]", "_", (upload.filename or "file").replace("\\", "/").split("/")[-1]
    )
    filename = filename[:160]
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(422, "Choose a text, CSV, PDF, PNG, or JPEG file.")
    data = upload.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Attachments must be no larger than 2 MiB.")
    if not data:
        raise HTTPException(422, "The attachment is empty.")
    storage_name = secrets.token_hex(20)
    path = media_path(settings.media_root, storage_name)
    derivative = media_path(settings.media_root, f"{storage_name}.txt")
    text = f"{filename}\n{len(data):,} bytes\nPreview is unavailable for this file type."
    if Path(filename).suffix.lower() in {".txt", ".md", ".csv"}:
        text = data[:32000].decode("utf-8", errors="replace")
    path.write_bytes(data)
    derivative.write_text(text, encoding="utf-8")
    attachment = Attachment(
        post_id=post.id,
        uploader_id=user.id,
        filename=filename,
        storage_name=storage_name,
        size=len(data),
        created_at=int(time.time()),
    )
    try:
        db.add(attachment)
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        derivative.unlink(missing_ok=True)
        raise
    return attachment
