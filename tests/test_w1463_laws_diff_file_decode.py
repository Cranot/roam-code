"""A diff the laws gate could not read must not report a clean gate.

``get_diff_text_status`` exists because ``get_diff_text`` collapsed four
distinct failures onto ``""`` -- the same value a genuinely clean diff
produces -- and its own docstring names the cost: "we could not look" reported
as "we looked and it was fine". The ``--diff-file`` branch then reached that
same outcome by a route the resulting guard cannot see.

The branch was ``read_text(encoding="utf-8", errors="replace")``. Feed it a
UTF-16LE diff -- which is what PowerShell 5.1's ``git diff > x.patch`` writes,
on the platform this repo is developed on -- and it returns, with
``error=None``, a string of U+FFFD and NULs. Measured on HEAD, one diff, two
encodings::

    utf-8  -> error=None  text_nonempty=True  files_parsed=1  added_lines=6
    utf-16 -> error=None  text_nonempty=True  files_parsed=0  added_lines=0

Every line of the mangled text classifies as ``other``, so ``parse_added``
opens no file, ``check_laws`` sees no added symbol, and the command prints
``VERDICT: 0 violations (0 blockers, 0 warnings, 0 advisories)`` with
``partial_success: False`` and exit 0 -- under ``--strict``, in CI.

The caller's fail-closed guard does not fire, and could not: it keys on ``not
diff_text.strip()``, and this text is not empty. U+FFFD and NUL are not
whitespace. The guard was built for the git-subprocess failures and the
mangled-decode path walks straight past it.

Note what the defect is NOT. It is not "UTF-16 is unsupported". It is that
recognising NOTHING in the input was indistinguishable from finding nothing
wrong with it -- so a wrong file passed to ``--diff-file``, a truncated
download, or a context-format diff all reported a clean gate just as loudly.
The fix is keyed to that: read the bytes with the readings this repo already
hardened for its credential gates, and if no reading contains a ``diff --git``
/ ``+++ b/`` / ``@@`` line, return an ERROR rather than text.

Every positive case carries a negative control in the same file, so a change
that merely makes the gate refuse everything cannot pass: a UTF-8 diff must
still read clean, and an EMPTY diff file must still be an error-free "no
changes" -- an empty diff is a real answer, not a failure to look.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.exit_codes import EXIT_GATE_FAILURE
from roam.laws.checker import get_diff_text_status, parse_added

_DIFF = """diff --git a/src/app/service.py b/src/app/service.py
index 1111111..2222222 100644
--- a/src/app/service.py
+++ b/src/app/service.py
@@ -1,3 +1,8 @@
 import os
+
+def BadlyNamedThing(x):
+    try:
+        return os.environ[x]
+    except Exception:
+        pass
"""


def _read(tmp_path, name, payload: bytes):
    """Write *payload* verbatim and run it through the real read path."""
    path = tmp_path / name
    path.write_bytes(payload)
    return get_diff_text_status(repo_root=tmp_path, diff_source="file", diff_file=str(path))


def _shape(text):
    """(files parsed, added lines) -- what the laws actually get to judge."""
    files = parse_added(text).get("files") or {}
    return len(files), sum(len(v.get("added_lines") or []) for v in files.values())


# ---------------------------------------------------------------------------
# The encoding that started this: it must now READ, not merely refuse.
# ---------------------------------------------------------------------------


def test_utf16_diff_file_parses_identically_to_its_utf8_twin(tmp_path):
    """Same diff, two encodings, one verdict.

    Refusing the UTF-16 file would also close the hole, but reading it is
    strictly better: the operator gets the laws evaluated instead of an error
    telling them to convert a file git itself produced.
    """
    utf8_text, utf8_err = _read(tmp_path, "a.patch", _DIFF.encode("utf-8"))
    utf16_text, utf16_err = _read(tmp_path, "b.patch", _DIFF.encode("utf-16"))

    assert utf8_err is None
    assert utf16_err is None
    assert _shape(utf16_text) == _shape(utf8_text)
    # Pre-fix this was (0, 0) against the control's (1, 6).
    assert _shape(utf16_text) == (1, 6)


def test_bomless_utf16_diff_file_is_still_recovered(tmp_path):
    """No BOM, so BOM dispatch alone cannot see it.

    The NUL-stripped reading is what catches this, and it is only available
    because the fix goes through ``decode_views`` rather than hand-rolling a
    single fallback.
    """
    payload = _DIFF.encode("utf-16-le")  # no BOM
    text, error = _read(tmp_path, "c.patch", payload)

    assert error is None
    assert _shape(text) == (1, 6)


# ---------------------------------------------------------------------------
# What we still cannot read must be DISCLOSED, not scored as clean.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,payload",
    [
        ("binary.patch", bytes(range(1, 256)) * 4),
        ("wrong-file.patch", json.dumps({"not": "a diff"}).encode("utf-8")),
        ("prose.patch", "just some notes about the change\nnothing structural\n".encode("utf-8")),
    ],
    ids=["binary", "wrong-file", "prose"],
)
def test_unreadable_content_reports_an_error_not_a_clean_read(tmp_path, name, payload):
    """Non-empty content with no diff structure is a failure to look.

    Pre-fix every one of these returned ``(garbage, None)`` -- a successful
    read -- and scored 0 violations.
    """
    text, error = _read(tmp_path, name, payload)

    assert error is not None, "unparseable bytes must not read as a successful diff"
    assert "not readable as a unified diff" in error
    # Text must be empty so the caller's existing guard can fire on it.
    assert text == ""


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the gate must not simply refuse everything.
# ---------------------------------------------------------------------------


def test_plain_utf8_diff_still_reads_clean(tmp_path):
    text, error = _read(tmp_path, "d.patch", _DIFF.encode("utf-8"))
    assert error is None
    assert _shape(text) == (1, 6)


def test_utf8_with_bom_still_reads_clean(tmp_path):
    text, error = _read(tmp_path, "e.patch", _DIFF.encode("utf-8-sig"))
    assert error is None
    assert _shape(text) == (1, 6)


@pytest.mark.parametrize("payload", [b"", b"   \n\n\t\n"], ids=["empty", "whitespace"])
def test_empty_diff_file_is_a_clean_answer_not_an_error(tmp_path, payload):
    """An empty diff means "no changes". That is a real answer, and the fix
    must not convert it into a refusal -- doing so would break every clean
    run and is exactly the over-correction this control exists to catch.
    """
    text, error = _read(tmp_path, "f.patch", payload)
    assert error is None
    assert text == ""


def test_missing_diff_file_keeps_its_own_distinct_error(tmp_path):
    """Pre-existing disclosure must survive the rewrite."""
    text, error = get_diff_text_status(repo_root=tmp_path, diff_source="file", diff_file=str(tmp_path / "nope.patch"))
    assert text == ""
    assert error is not None
    assert "could not read" in error


def test_diff_source_file_without_a_path_keeps_its_own_error(tmp_path):
    _text, error = get_diff_text_status(repo_root=tmp_path, diff_source="file", diff_file=None)
    assert error == "--diff-source file given without --diff-file"


# ---------------------------------------------------------------------------
# End to end: the exit code CI actually reads.
# ---------------------------------------------------------------------------

_LAWS_YML = """
laws:
  - id: naming-001
    kind: naming
    statement: functions are snake_case
    severity: blocker
    confidence: 0.99
    support: 50
    rule:
      kind: naming
      case: snake
      applies_to: function
"""


@pytest.fixture()
def laws_project(tmp_path):
    from tests.conftest import make_src_project

    proj = make_src_project(tmp_path, {"seed.py": "SEED = 1\n"})
    (proj / "roam-laws.yml").write_text(_LAWS_YML, encoding="utf-8")
    return proj


def _run_check(proj, monkeypatch, diff_bytes, name):
    from roam.cli import cli

    patch = proj / name
    patch.write_bytes(diff_bytes)
    monkeypatch.chdir(proj)
    return CliRunner().invoke(cli, ["laws", "check", "--diff-file", str(patch), "--strict"])


def test_strict_gate_fails_closed_on_an_unreadable_diff(laws_project, monkeypatch):
    """``laws check --strict`` on content it cannot parse.

    Pre-fix: ``VERDICT: 0 violations (0 blockers, ...)``, exit 0. A green CI
    gate over a diff nobody read.
    """
    result = _run_check(laws_project, monkeypatch, bytes(range(1, 256)) * 4, "bad.patch")

    assert result.exit_code == EXIT_GATE_FAILURE, result.output
    assert "DIFF UNAVAILABLE" in result.output
    assert "0 violations" not in result.output


def test_strict_gate_still_passes_a_readable_clean_diff(laws_project, monkeypatch):
    """Negative control on the gate: a diff it CAN read still exits 0.

    Uses a snake_case addition, so the blocker law loaded above has nothing
    to fire on and the run is genuinely clean rather than merely unread.
    """
    clean = (
        "diff --git a/src/app/ok.py b/src/app/ok.py\n"
        "--- a/src/app/ok.py\n"
        "+++ b/src/app/ok.py\n"
        "@@ -1,1 +1,2 @@\n"
        " import os\n"
        "+def well_named_thing():\n"
    )
    result = _run_check(laws_project, monkeypatch, clean.encode("utf-8"), "ok.patch")

    assert result.exit_code == 0, result.output
    assert "DIFF UNAVAILABLE" not in result.output


def test_strict_gate_reads_a_utf16_diff_end_to_end(laws_project, monkeypatch):
    """The full path: a PowerShell-written patch reaches the laws.

    Exit code is not asserted -- whether this specific diff trips a blocker is
    the laws' business, not this test's. What is asserted is that the gate
    stopped reporting DIFF UNAVAILABLE *and* stopped silently scoring an
    unread file, i.e. that real content reached ``check_laws``.
    """
    result = _run_check(laws_project, monkeypatch, _DIFF.encode("utf-16"), "ps.patch")

    assert "DIFF UNAVAILABLE" not in result.output
    assert "violations" in result.output
