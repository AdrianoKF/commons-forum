import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from .content import PostEdit, PostUpdate, apply_update, message_text, parse_fields
from .models import Attachment, AuditEvent, Board, Membership, Post, Reply, User
from .policy import (
    board_for_view,
    ensure_admin,
    ensure_edit,
    ensure_open,
    ensure_staff,
    is_staff,
    post_for_view,
    visible_boards,
)
from .security import (
    check_csrf,
    current_user,
    database,
    hash_password,
    identity,
    require_user,
    rotate_session,
    verify_password,
)
from .storage import original_path, preview_path, store_attachment

router = APIRouter(dependencies=[Depends(check_csrf)])


def page(request, template, user, session, **context):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=template,
        context={"user": user, "csrf": session.csrf_token, "staff": is_staff(user), **context},
    )


def redirect(path):
    return RedirectResponse(path, status_code=303)


def audit(db, user, action, subject):
    db.add(
        AuditEvent(actor_id=user.id, action=action, subject=subject, created_at=int(time.time()))
    )


@router.get("/")
def home(
    request: Request, user=Depends(current_user), session=Depends(identity), db=Depends(database)
):
    boards = db.scalars(visible_boards(user).order_by(Board.id)).all()
    return page(request, "home.html", user, session, boards=boards)


@router.get("/register")
@router.get("/login")
def auth_form(request: Request, user=Depends(current_user), session=Depends(identity)):
    return page(request, "auth.html", user, session, registering=request.url.path == "/register")


@router.post("/register")
async def register(request: Request, session=Depends(identity), db=Depends(database)):
    form = await request.form()
    username = str(form.get("username", "")).strip().lower()
    password = str(form.get("password", ""))
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username) or not 12 <= len(password) <= 128:
        raise HTTPException(422, "Use a 3–32 character username and a 12–128 character password.")
    user = User(username=username, password_hash=hash_password(password), role="member")
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That username is already registered.")
    rotate_session(db, request, session, user.id)
    return redirect("/")


@router.post("/login")
async def login(request: Request, session=Depends(identity), db=Depends(database)):
    form = await request.form()
    username = str(form.get("username", "")).strip().lower()
    password = str(form.get("password", ""))
    if len(username) > 32 or len(password) > 128:
        raise HTTPException(401, "Username or password was not recognized.")
    user = db.scalar(select(User).where(User.username == username))
    encoded = user.password_hash if user else request.app.state.dummy_password_hash
    if not verify_password(password, encoded) or user is None:
        raise HTTPException(401, "Username or password was not recognized.")
    rotate_session(db, request, session, user.id)
    return redirect("/")


@router.post("/logout")
def logout(request: Request, session=Depends(identity), db=Depends(database)):
    rotate_session(db, request, session)
    return redirect("/")


@router.get("/boards/{board_id}")
def board_page(
    board_id: int,
    request: Request,
    user=Depends(current_user),
    session=Depends(identity),
    db=Depends(database),
):
    board = board_for_view(db, user, board_id)
    query = select(Post).where(Post.board_id == board.id)
    if not is_staff(user):
        query = query.where(Post.hidden.is_(False))
    posts = db.scalars(
        query.order_by(Post.pinned.desc(), Post.updated_at.desc(), Post.id.desc())
    ).all()
    return page(request, "board.html", user, session, board=board, posts=posts)


@router.get("/search")
def search(
    request: Request,
    q: str = "",
    user=Depends(current_user),
    session=Depends(identity),
    db=Depends(database),
):
    q = q.strip()[:120]
    boards = visible_boards(user).with_only_columns(Board.id)
    query = select(Post).where(Post.board_id.in_(boards))
    if not is_staff(user):
        query = query.where(Post.hidden.is_(False))
    query = query.where(
        or_(Post.title.contains(q, autoescape=True), Post.body.contains(q, autoescape=True))
    )
    posts = db.scalars(query.order_by(Post.updated_at.desc()).limit(50)).all() if q else []
    return page(request, "search.html", user, session, q=q, posts=posts)


@router.get("/boards/{board_id}/new")
def new_post_form(
    board_id: int,
    request: Request,
    user=Depends(require_user),
    session=Depends(identity),
    db=Depends(database),
):
    board = board_for_view(db, user, board_id)
    return page(request, "edit_post.html", user, session, board=board, post=None)


@router.post("/boards/{board_id}/posts")
async def create_post(
    board_id: int, request: Request, user=Depends(require_user), db=Depends(database)
):
    board = board_for_view(db, user, board_id)
    fields = parse_fields(PostEdit, await request.form())
    now = int(time.time())
    post = Post(
        board_id=board.id,
        author_id=user.id,
        title=fields.title,
        body=fields.body,
        created_at=now,
        updated_at=now,
    )
    db.add(post)
    db.commit()
    return redirect(f"/posts/{post.id}")


@router.get("/posts/{post_id}")
def post_page(
    post_id: int,
    request: Request,
    user=Depends(current_user),
    session=Depends(identity),
    db=Depends(database),
):
    post = post_for_view(db, user, post_id)
    replies = db.scalars(select(Reply).where(Reply.post_id == post.id).order_by(Reply.id)).all()
    attachments = db.scalars(
        select(Attachment).where(Attachment.post_id == post.id).order_by(Attachment.id)
    ).all()
    editable = user is not None and (
        is_staff(user) or (user.id == post.author_id and not post.locked)
    )
    return page(
        request,
        "post.html",
        user,
        session,
        post=post,
        replies=replies,
        attachments=attachments,
        editable=editable,
    )


@router.get("/posts/{post_id}/edit")
def edit_post_form(
    post_id: int,
    request: Request,
    user=Depends(require_user),
    session=Depends(identity),
    db=Depends(database),
):
    post = post_for_view(db, user, post_id)
    ensure_edit(user, post)
    return page(request, "edit_post.html", user, session, board=post.board, post=post)


@router.post("/posts/{post_id}/edit")
async def edit_post(
    post_id: int, request: Request, user=Depends(require_user), db=Depends(database)
):
    post = post_for_view(db, user, post_id)
    ensure_edit(user, post)
    changes = parse_fields(PostUpdate, await request.form())
    apply_update(post, changes)
    db.commit()
    return redirect(f"/posts/{post.id}")


@router.post("/posts/{post_id}/replies")
async def create_reply(
    post_id: int, request: Request, user=Depends(require_user), db=Depends(database)
):
    post = post_for_view(db, user, post_id)
    ensure_open(user, post)
    form = await request.form()
    body = message_text(form.get("body", ""))
    now = int(time.time())
    reply = Reply(
        post_id=post.id,
        author_id=user.id,
        body=body,
        quote_post=form.get("quote_post") == "1",
        created_at=now,
        updated_at=now,
    )
    db.add(reply)
    post.updated_at = now
    db.commit()
    return redirect(f"/posts/{post.id}#reply-{reply.id}")


@router.get("/replies/{reply_id}/edit")
def edit_reply_form(
    reply_id: int,
    request: Request,
    user=Depends(require_user),
    session=Depends(identity),
    db=Depends(database),
):
    reply = db.get(Reply, reply_id)
    if reply is None:
        raise HTTPException(404, "Reply not found.")
    post = post_for_view(db, user, reply.post_id)
    ensure_edit(user, post, reply.author_id)
    return page(request, "edit_reply.html", user, session, reply=reply, post=post)


@router.post("/replies/{reply_id}/edit")
async def edit_reply(
    reply_id: int, request: Request, user=Depends(require_user), db=Depends(database)
):
    reply = db.get(Reply, reply_id)
    if reply is None:
        raise HTTPException(404, "Reply not found.")
    post = post_for_view(db, user, reply.post_id)
    ensure_edit(user, post, reply.author_id)
    reply.body = message_text((await request.form()).get("body", ""))
    reply.updated_at = int(time.time())
    db.commit()
    return redirect(f"/posts/{post.id}#reply-{reply.id}")


@router.post("/posts/{post_id}/attachments")
async def upload_attachment(
    post_id: int, request: Request, user=Depends(require_user), db=Depends(database)
):
    post = post_for_view(db, user, post_id)
    ensure_edit(user, post)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "file"):
        raise HTTPException(422, "Choose a file to attach.")
    store_attachment(db, request.app.state.settings, post, user, upload)
    return redirect(f"/posts/{post.id}")


def attachment_for_view(db, user, attachment_id):
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "Attachment not found.")
    post_for_view(db, user, attachment.post_id)
    return attachment


@router.get("/attachments/{attachment_id}/download")
def download(
    attachment_id: int, request: Request, user=Depends(current_user), db=Depends(database)
):
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "Attachment not found.")
    path = original_path(request.app.state.settings, attachment)
    return FileResponse(path, media_type="application/octet-stream", filename=attachment.filename)


@router.get("/attachments/{attachment_id}/preview")
def preview(
    attachment_id: int,
    request: Request,
    variant: str = "",
    user=Depends(current_user),
    session=Depends(identity),
    db=Depends(database),
):
    attachment = attachment_for_view(db, user, attachment_id)
    path = preview_path(request.app.state.settings, attachment, variant)
    with path.open(encoding="utf-8", errors="replace") as stream:
        content = stream.read(32000)
    return page(request, "preview.html", user, session, attachment=attachment, content=content)


@router.post("/attachments/{attachment_id}/delete")
def delete_attachment(
    attachment_id: int, request: Request, user=Depends(require_user), db=Depends(database)
):
    attachment = attachment_for_view(db, user, attachment_id)
    ensure_edit(user, attachment.post)
    settings = request.app.state.settings
    original = original_path(settings, attachment)
    derived = preview_path(settings, attachment, "")
    post_id = attachment.post_id
    db.delete(attachment)
    db.commit()
    original.unlink(missing_ok=True)
    derived.unlink(missing_ok=True)
    return redirect(f"/posts/{post_id}")


@router.post("/posts/{post_id}/moderate")
async def moderate(
    post_id: int, request: Request, user=Depends(require_user), db=Depends(database)
):
    ensure_staff(user)
    post = post_for_view(db, user, post_id)
    changes = parse_fields(PostUpdate, await request.form())
    apply_update(post, changes)
    audit(db, user, "moderate discussion", str(post.id))
    db.commit()
    return redirect(f"/posts/{post.id}")


@router.get("/admin")
def admin_page(
    request: Request, user=Depends(require_user), session=Depends(identity), db=Depends(database)
):
    ensure_admin(user)
    return page(
        request,
        "admin.html",
        user,
        session,
        users=db.scalars(select(User).order_by(User.username)).all(),
        boards=db.scalars(select(Board).order_by(Board.id)).all(),
        memberships=db.scalars(select(Membership)).all(),
        events=db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(30)).all(),
    )


@router.post("/admin/memberships")
async def set_membership(request: Request, user=Depends(require_user), db=Depends(database)):
    ensure_admin(user)
    form = await request.form()
    try:
        user_id, board_id = int(str(form.get("user_id"))), int(str(form.get("board_id")))
    except (TypeError, ValueError):
        raise HTTPException(422, "Choose a user and board.")
    if db.get(User, user_id) is None or db.get(Board, board_id) is None:
        raise HTTPException(404, "User or board not found.")
    existing = db.get(Membership, (user_id, board_id))
    if form.get("action") == "grant":
        if existing is None:
            db.add(Membership(user_id=user_id, board_id=board_id))
    elif form.get("action") == "revoke":
        if existing is not None:
            db.delete(existing)
    else:
        raise HTTPException(422, "Choose grant or revoke.")
    audit(db, user, f"{form['action']} board membership", f"user {user_id}, board {board_id}")
    db.commit()
    return redirect("/admin")
