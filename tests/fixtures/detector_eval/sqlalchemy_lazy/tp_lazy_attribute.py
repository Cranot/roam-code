"""True positive: SQLAlchemy ``.all()`` then attribute access on items."""

from __future__ import annotations

from sqlalchemy.orm import relationship


def list_user_emails(session):
    # py-sqlalchemy-lazy: .all() then attribute access -> lazy load N+1
    users = session.query(User).all()
    for u in users:
        print(u.profile.email)


def list_post_titles(session):
    # py-sqlalchemy-lazy: same pattern, different relationship
    posts = session.query(Post).all()
    for p in posts:
        print(p.author.name)


class User:
    profile = relationship("Profile")


class Post:
    author = relationship("User")


class Profile:
    pass
