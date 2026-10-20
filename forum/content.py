import time

from fastapi import HTTPException
from markupsafe import Markup, escape
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PostEdit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=20000)


class PostUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    pinned: bool | None = None
    locked: bool | None = None
    hidden: bool | None = None


def parse_fields(schema, form):
    try:
        return schema.model_validate(dict(form))
    except ValidationError:
        raise HTTPException(
            422, "Check the form: a title and message within the limits are required."
        )


def apply_update(post, changes):
    for field, value in changes.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(post, field, value)
    post.updated_at = int(time.time())


def message_text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 20000:
        raise HTTPException(422, "Messages must contain 1–20,000 characters.")
    return value.strip()


def render_body(body):
    return Markup("<br>\n").join(escape(body).splitlines())


def render_quote(body):
    return Markup(body.replace("\n", "<br>\n"))
