"""Intent words embedded in other words must not trigger mismatch reports."""

import pytest

from roam.critique.checks import ChangedRegion, ChangedSymbol, check_intent_alignment


def _check(title, additions, deletions):
    changed = [ChangedSymbol(1, "parse", "parse", "function", "parser.py", 1, 10)]
    regions = [ChangedRegion("parser.py", ((1, 10),), additions, deletions)]
    return check_intent_alignment(title, changed, regions)


@pytest.mark.parametrize(
    "title, additions, deletions",
    [
        ("Update address parsing", 0, 4),
        ("Adjust padding", 0, 4),
        ("Update prefix handling", 10, 0),
        ("Update suffix handling", 10, 0),
    ],
)
def test_embedded_verbs_do_not_imply_intent(title, additions, deletions):
    assert _check(title, additions, deletions) == []


@pytest.mark.parametrize("title", ["ADD address parsing", "feat: add(address parsing)"])
def test_explicit_add_still_flags_deletion_only_diff(title):
    assert _check(title, 0, 4)


def test_explicit_add_with_additions_is_aligned():
    assert _check("Add address parsing", 4, 0) == []


@pytest.mark.parametrize(
    "verb", ["Added", "Adds", "Adding", "Introduced", "Creates", "Supporting", "Implemented", "Shipped"]
)
def test_inflected_add_intent(verb):
    assert _check(verb + " functionality", 0, 4)


@pytest.mark.parametrize(
    "verb", ["Removed", "Removes", "Removing", "Deleted", "Deletes", "Deleting", "Dropped", "Dropping", "Retired"]
)
def test_inflected_remove_intent(verb):
    assert _check(verb + " functionality", 4, 0)
