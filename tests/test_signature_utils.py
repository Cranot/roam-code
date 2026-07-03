"""Tests for roam._signature_utils -- signature-string param parsing.

Covers parse_param_names across the behaviors documented in its docstring:
empty/None/unparseable handling, comma-splitting with bracket-depth
tracking, annotation/default stripping, ``*``/``**`` markers, and the
``_IGNORED_PARAM_NAMES`` receiver/placeholder filter. Also pins a few
quirks (positional-only ``/`` survives; nested-paren defaults are
regex-truncated) so future refactors of the parser are forced to be
intentional about them.
"""

from __future__ import annotations

import pytest

from roam._signature_utils import _IGNORED_PARAM_NAMES, parse_param_names

# ---------------------------------------------------------------------------
# Empty / falsy / unparseable inputs -> []
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sig",
    [
        None,
        "",
        "no parentheses at all",
        "def foo()",  # empty arg list
        "foo(   )",  # whitespace-only arg list
    ],
)
def test_returns_empty_list_for_no_params(sig):
    assert parse_param_names(sig) == []


# ---------------------------------------------------------------------------
# Basic comma splitting
# ---------------------------------------------------------------------------


def test_simple_params():
    assert parse_param_names("def foo(a, b, c)") == ["a", "b", "c"]


def test_single_param():
    assert parse_param_names("foo(x)") == ["x"]


def test_default_values_are_stripped():
    assert parse_param_names("foo(a, b=1, c='x')") == ["a", "b", "c"]


def test_type_annotations_are_stripped():
    assert parse_param_names("foo(a: int, b: str)") == ["a", "b"]


def test_annotation_and_default_together():
    assert parse_param_names("foo(a: int = 5, b: str = 'z')") == ["a", "b"]


# ---------------------------------------------------------------------------
# Star markers: leading * / ** are removed, names preserved
# ---------------------------------------------------------------------------


def test_args_and_kwargs():
    assert parse_param_names("foo(*args, **kwargs)") == ["args", "kwargs"]


def test_double_star_only():
    assert parse_param_names("foo(**kwargs)") == ["kwargs"]


def test_mixed_positional_and_star():
    assert parse_param_names("foo(a, b, *rest, **opts)") == ["a", "b", "rest", "opts"]


def test_bare_star_keyword_only_marker_is_dropped():
    # ``*`` alone (PEP 3102 keyword-only separator) reduces to "" and is skipped.
    assert parse_param_names("foo(a, *, b)") == ["a", "b"]


# ---------------------------------------------------------------------------
# Bracket-depth tracking: commas inside [] {} () <> do not split params
# ---------------------------------------------------------------------------


def test_nested_generic_with_inner_commas_is_one_param():
    assert parse_param_names("foo(cb: Callable[[int, str], None])") == ["cb"]


def test_nested_generic_among_other_params():
    sig = "foo(a, cb: Callable[[int, str], None], b)"
    assert parse_param_names(sig) == ["a", "cb", "b"]


def test_dict_annotation_inner_comma():
    assert parse_param_names("foo(a: Dict[str, int], b: int)") == ["a", "b"]


def test_angle_bracket_generic():
    # ``<`` / ``>`` are tracked too (e.g. Java/C++-style generics).
    assert parse_param_names("foo(a: Map<int, str>, b)") == ["a", "b"]


def test_brace_default_inner_comma():
    assert parse_param_names("foo(a={'x': 1, 'y': 2}, b)") == ["a", "b"]


# ---------------------------------------------------------------------------
# _IGNORED_PARAM_NAMES filtering
# ---------------------------------------------------------------------------


def test_ignored_receiver_and_placeholder_names_filtered():
    assert parse_param_names("def m(self, cls, this, _, a)") == ["a"]


def test_all_ignored_yields_empty_list():
    assert parse_param_names("def m(self, _)") == []


def test_ignore_set_membership():
    assert _IGNORED_PARAM_NAMES == frozenset({"_", "self", "cls", "this"})


def test_ignore_filter_is_case_sensitive():
    # Only the exact lowercase tokens are ignored; ``Self`` is a real name.
    assert parse_param_names("foo(Self, Cls, a)") == ["Self", "Cls", "a"]


# ---------------------------------------------------------------------------
# Empty fragments between commas are skipped
# ---------------------------------------------------------------------------


def test_empty_fragment_between_commas_skipped():
    assert parse_param_names("foo(a, , b)") == ["a", "b"]


def test_trailing_comma():
    assert parse_param_names("foo(a, b,)") == ["a", "b"]


# ---------------------------------------------------------------------------
# Pinned quirks (regression guards on documented limitations)
# ---------------------------------------------------------------------------


def test_positional_only_slash_marker_survives():
    # ``/`` is not stripped or ignored, so it leaks through as a "name".
    # Pinned so a future cleanup that drops it is a deliberate change.
    assert parse_param_names("foo(a, /, b)") == ["a", "/", "b"]


def test_paren_in_default_is_depth_tracked_not_split():
    # The trailing ``)`` of the default lands inside depth>0, so the inner
    # comma does not split and only the name survives.
    assert parse_param_names("foo(a: Tuple[int, ...] = (1, 2))") == ["a"]


def test_only_first_paren_group_is_parsed():
    # The regex captures the first (...) group; later groups are ignored.
    assert parse_param_names("foo(a, b) -> Dict[str, int]") == ["a", "b"]


# ---------------------------------------------------------------------------
# Return-type / ordering contract
# ---------------------------------------------------------------------------


def test_order_is_preserved():
    assert parse_param_names("foo(z, m, a)") == ["z", "m", "a"]


def test_returns_plain_list():
    result = parse_param_names("foo(a, b)")
    assert isinstance(result, list)
    assert all(isinstance(n, str) for n in result)
