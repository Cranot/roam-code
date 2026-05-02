"""True negative: collection-style ``.all()`` outside Django ORM.

Custom collection classes (or pathlib, generic ORMs that aren't Django)
shouldn't trigger py-django-n1. The detector should require a Django
hint in the file before firing.
"""

from __future__ import annotations


class CustomCollection:
    """Not a Django manager — just a class with .all()."""

    def all(self):
        return []


def operate_on_collection(collection):
    matches = collection.all()
    for m in matches:
        print(m)
