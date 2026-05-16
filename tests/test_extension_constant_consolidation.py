"""W37.5 / W39.4 pins: extension-constant consolidation.

The W37.2 citation-lint surfaced 3 _DOC_EXTENSIONS copies (file_roles,
cmd_intent, cmd_secrets) with divergent contents (5 / 4 / 3 entries
respectively). W37.5 consolidated them onto roam.index.file_roles.DOC_EXTENSIONS
(5-entry canonical union). It also surfaced cmd_secrets._BINARY_EXTENSIONS as
a near-duplicate of roam.index.discovery.SKIP_EXTENSIONS; W37.5 kept them
separate under a "semantically distinct" rule and pinned the subset
relationship. W39.4 ended the multi-wave ambiguity by collapsing
_BINARY_EXTENSIONS to an alias of SKIP_EXTENSIONS — the 5-extra distinction
(.lock / .map / .min.js / .min.css / .sct) was not load-bearing (no test
asserted secrets-scan processed any of those, and .lock files are also
filtered by SKIP_NAMES so the lock-via-extension branch was redundant).

These tests prevent future drift by enforcing the single-source-of-truth
relationships established by W37.5 and W39.4.
"""

from __future__ import annotations


def test_doc_extensions_single_source_of_truth():
    """``_DOC_EXTENSIONS`` in cmd_intent and cmd_secrets must be the same
    object as ``roam.index.file_roles.DOC_EXTENSIONS`` (W37.5).

    Catches future drift if a maintainer redefines the constant locally
    instead of importing it.
    """
    from roam.commands import cmd_intent, cmd_secrets
    from roam.index.file_roles import DOC_EXTENSIONS

    assert cmd_intent._DOC_EXTENSIONS is DOC_EXTENSIONS, (
        "cmd_intent._DOC_EXTENSIONS must BE (is) the canonical "
        "roam.index.file_roles.DOC_EXTENSIONS frozenset, not a local copy."
    )
    assert cmd_secrets._DOC_EXTENSIONS is DOC_EXTENSIONS, (
        "cmd_secrets._DOC_EXTENSIONS must BE (is) the canonical "
        "roam.index.file_roles.DOC_EXTENSIONS frozenset, not a local copy."
    )


def test_doc_extensions_public_alias_matches_private():
    """``file_roles._DOC_EXTENSIONS`` must remain an alias of the public
    ``DOC_EXTENSIONS`` for backwards compatibility with any in-module callers.
    """
    from roam.index import file_roles

    assert file_roles._DOC_EXTENSIONS is file_roles.DOC_EXTENSIONS, (
        "file_roles._DOC_EXTENSIONS must be an alias of the public DOC_EXTENSIONS (not a separate frozenset)."
    )


def test_doc_extensions_canonical_contents():
    """Pin the canonical 5-entry set, so future edits to DOC_EXTENSIONS get
    a deliberate test failure forcing a citation update.
    """
    from roam.index.file_roles import DOC_EXTENSIONS

    assert DOC_EXTENSIONS == frozenset({".md", ".rst", ".adoc", ".asciidoc", ".txt"}), (
        "DOC_EXTENSIONS canonical set must contain 5 entries: "
        ".md, .rst, .adoc, .asciidoc, .txt. If you need to add or remove an "
        "extension, update the citation comment in file_roles.py and this "
        "test together."
    )


def test_binary_extensions_aliased_to_skip_extensions():
    """``cmd_secrets._BINARY_EXTENSIONS`` is the SAME object as
    ``roam.index.discovery.SKIP_EXTENSIONS`` after the W39.4 collapse.

    History: W37.5 pinned _BINARY_EXTENSIONS (48 entries) as a strict subset
    of SKIP_EXTENSIONS (53 entries) and kept them semantically distinct.
    W39.4 ended the multi-wave ambiguity by collapsing to one source of
    truth — the 5-extra distinction (.lock / .map / .min.js / .min.css /
    .sct) was not load-bearing (no test asserted secrets-scan should
    process any of those, and .lock files are already filtered by
    SKIP_NAMES, making the extension-based branch redundant).

    If a maintainer needs a distinct binary-only set in future, define
    ``_BINARY_EXTENSIONS_NARROW`` with its own citation; do NOT re-fork
    ``_BINARY_EXTENSIONS`` from this alias — that re-creates the drift
    W39.4 closed.
    """
    from roam.commands.cmd_secrets import _BINARY_EXTENSIONS
    from roam.index.discovery import SKIP_EXTENSIONS

    assert _BINARY_EXTENSIONS is SKIP_EXTENSIONS, (
        "_BINARY_EXTENSIONS must BE (is) roam.index.discovery.SKIP_EXTENSIONS "
        "after the W39.4 collapse. If you need a distinct binary-only set, "
        "define _BINARY_EXTENSIONS_NARROW and document why."
    )
