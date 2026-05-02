"""True negatives for the SQLAlchemy lazy-load detector."""

from __future__ import annotations

from sqlalchemy.orm import joinedload, relationship


def list_with_joinedload(session):
    # Eager loading via joinedload — defuses the lazy-load
    users = session.query(User).options(joinedload(User.profile)).all()
    for u in users:
        print(u.profile.email)


def list_with_selectinload(session):
    # Eager loading via selectinload — same idea
    from sqlalchemy.orm import selectinload

    posts = session.query(Post).options(selectinload(Post.author)).all()
    for p in posts:
        print(p.author.name)


def no_attribute_access(session):
    # .all() then iterate but no attribute access — fine
    users = session.query(User).all()
    for u in users:
        print(u)


class User:
    profile = relationship("Profile")


class Post:
    author = relationship("User")


class Profile:
    pass
