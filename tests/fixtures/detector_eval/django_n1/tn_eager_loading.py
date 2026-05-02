"""True negative: ``.all()`` then iterate WITH eager loading — no N+1."""

from __future__ import annotations


def show_titles_eager():
    # Eager-loaded — should NOT fire (select_related is on the qs)
    posts = Post.objects.select_related("author").all()
    for post in posts:
        print(post.author.name)


def show_with_prefetch():
    # Same: prefetch_related stops the N+1
    posts = Post.objects.prefetch_related("comments").all()
    for post in posts:
        print(post.comments.count())


def query_outside_loop(user_id):
    # Single query OUTSIDE loop — fine
    profile = Profile.objects.get(user_id=user_id)
    for tag in profile.tags:
        print(tag)


def in_bulk_lookup(user_ids):
    # ``.in_bulk()`` is the *fix* the detector recommends — must not fire
    lookup = Profile.objects.in_bulk(user_ids)
    for uid in user_ids:
        print(lookup.get(uid))


class Post:
    pass


class Profile:
    pass
