from fastapi import HTTPException
from sqlalchemy import or_, select

from .models import Board, Membership, Post


def is_staff(user):
    return user is not None and user.role in {"moderator", "admin"}


def visible_boards(user):
    query = select(Board)
    if is_staff(user):
        return query
    if user is None:
        return query.where(Board.private.is_(False))
    memberships = select(Membership.board_id).where(Membership.user_id == user.id)
    return query.where(or_(Board.private.is_(False), Board.id.in_(memberships)))


def can_view_board(db, user, board):
    return (
        not board.private
        or is_staff(user)
        or (user is not None and db.get(Membership, (user.id, board.id)) is not None)
    )


def board_for_view(db, user, board_id):
    board = db.get(Board, board_id)
    if board is None or not can_view_board(db, user, board):
        raise HTTPException(404, "Board not found.")
    return board


def post_for_view(db, user, post_id):
    post = db.get(Post, post_id)
    if post is None or not can_view_board(db, user, post.board):
        raise HTTPException(404, "Discussion not found.")
    if post.hidden and not is_staff(user):
        raise HTTPException(404, "Discussion not found.")
    return post


def ensure_edit(user, post, author_id=None):
    if user is None or (not is_staff(user) and user.id != (author_id or post.author_id)):
        raise HTTPException(403, "Only the author or a moderator can edit this content.")
    ensure_open(user, post)


def ensure_open(user, post):
    if post.locked and not is_staff(user):
        raise HTTPException(403, "This discussion is locked.")


def ensure_staff(user):
    if not is_staff(user):
        raise HTTPException(403, "Moderator access required.")


def ensure_admin(user):
    if user is None or user.role != "admin":
        raise HTTPException(403, "Administrator access required.")
