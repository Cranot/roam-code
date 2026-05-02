"""True positive: Django ORM filter inside loop -> N+1 query."""

from __future__ import annotations


def list_orders_for_users(user_ids):
    out = []
    for uid in user_ids:
        # py-django-n1: filter inside the loop
        order = Order.objects.filter(user_id=uid).first()
        out.append(order)
    return out


def render_dashboard(users):
    rows = []
    for user in users:
        # py-django-n1: get inside the loop
        profile = Profile.objects.get(user=user)
        rows.append((user, profile))
    return rows


class Order:
    pass


class Profile:
    pass
