"""True positive: ``.all()`` then iterate without prefetch."""

from __future__ import annotations


def show_titles():
    # py-django-n1: all-then-for, no select_related/prefetch_related
    posts = Post.objects.all()
    for post in posts:
        print(post.author.name)


def naive_summary():
    # py-django-n1: same shape, different model
    invoices = Invoice.objects.all()
    for inv in invoices:
        print(inv.customer.email)


class Post:
    pass


class Invoice:
    pass
