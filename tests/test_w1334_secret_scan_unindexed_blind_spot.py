"""A secret scan must not certify files it never opened.

``roam secrets`` is index-backed: it takes its file list from the roam index DB,
then reads each of those paths from disk. That makes a *modified* file safe --
it is re-read with current content -- but a file created since the last
``roam index`` is not in the list at all, so it is never scanned. The scan then
printed ``VERDICT: No secrets found (N files scanned)`` and exited 0.

The gap is not academic, and it is the worst possible shape for this particular
command: a freshly-pasted credential is *by definition* in a file the index has
not caught up with. Measured on the roam-code repo itself, same file and same
command, the only variable being index freshness:

    before `roam index`   VERDICT: No secrets found (3294 files scanned)   exit 0
    after  `roam index`   VERDICT: 2 secrets found in 1 files (2 high)     exit 5

Both gates that consume this are blocking: ``--fail-on-found`` gates CI, and the
check rides ``verify --auto`` and therefore the Claude Stop hook.

The staleness signal already existed -- ``_meta.index_status`` carried
``fresh: false`` in the JSON envelope the whole time. It was simply never
mirrored into the text branch, which is the surface a CI log and a human
actually read. ``index_status()``'s own docstring specifies printing it
"before the VERDICT in text mode"; that contract was unimplemented here.

Precision matters as much as loudness, so the negative controls below are load
bearing: a clean, freshly-indexed project must still report clean, and merely
*editing* an already-indexed file must NOT trip the new signal. A change that
made everything shout would pass the positive tests and fail these.

Credential-shaped strings are assembled at RUNTIME -- the repo's own secret
gate correctly blocks literals, and bypassing it is not an option.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

# Split so no line here is itself a credential-shaped literal.
_CREDENTIAL = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8S9t0"
_LEAK_SOURCE = f'VALUE = "{_CREDENTIAL}"\n'


def _invoke(args, cwd, json_mode: bool = False):
    from roam.cli import cli

    full_args = (["--json"] if json_mode else []) + list(args)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return CliRunner().invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _summary(cwd) -> dict:
    result = _invoke(["secrets", "--severity", "all"], cwd, json_mode=True)
    return json.loads(result.output).get("summary", {})


@pytest.fixture
def indexed_clean_project(project_factory):
    """A project with no secrets, indexed at creation time."""
    return project_factory(
        {
            "app.py": "def main():\n    return 1\n",
            "utils.py": "def add(a, b):\n    return a + b\n",
        }
    )


# ---------------------------------------------------------------------------
# _count_unindexed -- the primitive
# ---------------------------------------------------------------------------


def test_count_unindexed_is_zero_when_the_index_covers_everything(indexed_clean_project):
    """Freshly indexed means nothing is missed -- the signal must stay silent."""
    from roam.commands.cmd_secrets import _count_unindexed
    from roam.db.connection import open_db

    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_clean_project))
        with open_db(readonly=True) as conn:
            indexed = [row["path"] for row in conn.execute("SELECT path FROM files").fetchall()]
        assert _count_unindexed(indexed_clean_project, indexed) == 0
    finally:
        os.chdir(old_cwd)


def test_count_unindexed_counts_a_file_the_index_has_never_seen(indexed_clean_project):
    from roam.commands.cmd_secrets import _count_unindexed
    from roam.db.connection import open_db

    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_clean_project))
        with open_db(readonly=True) as conn:
            indexed = [row["path"] for row in conn.execute("SELECT path FROM files").fetchall()]
        (indexed_clean_project / "brand_new.py").write_text(_LEAK_SOURCE, encoding="utf-8")
        assert _count_unindexed(indexed_clean_project, indexed) == 1
    finally:
        os.chdir(old_cwd)


def test_count_unindexed_returns_none_not_zero_when_it_cannot_tell(indexed_clean_project, monkeypatch):
    """ "I could not determine" must never be encoded as "nothing was missed"."""
    import roam.index.discovery as discovery
    from roam.commands.cmd_secrets import _count_unindexed

    def _boom(*_args, **_kwargs):
        raise OSError("discovery unavailable")

    monkeypatch.setattr(discovery, "discover_files", _boom)
    assert _count_unindexed(indexed_clean_project, []) is None


# ---------------------------------------------------------------------------
# The command -- positive cases
# ---------------------------------------------------------------------------


def test_unindexed_credential_is_not_reported_as_clean(indexed_clean_project):
    """The headline defect: a credential the index never saw read as 'clean'."""
    (indexed_clean_project / "leaked.py").write_text(_LEAK_SOURCE, encoding="utf-8")

    result = _invoke(["secrets", "--severity", "all"], indexed_clean_project)
    assert "No secrets found" not in result.output
    assert "NOT PROVEN CLEAN" in result.output


def test_unindexed_file_marks_the_scan_incomplete_in_json(indexed_clean_project):
    (indexed_clean_project / "leaked.py").write_text(_LEAK_SOURCE, encoding="utf-8")

    summary = _summary(indexed_clean_project)
    assert summary["files_unindexed"] >= 1
    assert summary["scan_incomplete"] is True
    assert summary["partial_success"] is True


def test_fail_on_found_refuses_when_files_were_never_scanned(indexed_clean_project):
    """The gate must fail closed: an unproven clean is not a pass."""
    (indexed_clean_project / "leaked.py").write_text(_LEAK_SOURCE, encoding="utf-8")

    result = _invoke(["secrets", "--severity", "all", "--fail-on-found"], indexed_clean_project)
    assert result.exit_code != 0


def test_text_and_json_agree_that_the_scan_was_incomplete(indexed_clean_project):
    """Disclosure parity: the asymmetry between surfaces is what hid this."""
    (indexed_clean_project / "leaked.py").write_text(_LEAK_SOURCE, encoding="utf-8")

    text = _invoke(["secrets", "--severity", "all"], indexed_clean_project).output
    assert "NOT a clean result" in text or "NOT PROVEN CLEAN" in text
    assert self_consistent(text, _summary(indexed_clean_project))


def self_consistent(text: str, summary: dict) -> bool:
    """Text claims incompleteness exactly when the envelope does."""
    text_says_incomplete = "NOT PROVEN CLEAN" in text or "NOT a clean result" in text
    return text_says_incomplete is bool(summary.get("scan_incomplete"))


# ---------------------------------------------------------------------------
# Negative controls -- a change that merely shouts must fail these
# ---------------------------------------------------------------------------


def test_a_freshly_indexed_clean_project_still_reports_clean(indexed_clean_project):
    result = _invoke(["secrets", "--severity", "all"], indexed_clean_project)
    assert "No secrets found" in result.output
    assert "NOT PROVEN CLEAN" not in result.output


def test_a_freshly_indexed_clean_project_still_passes_the_gate(indexed_clean_project):
    result = _invoke(["secrets", "--severity", "all", "--fail-on-found"], indexed_clean_project)
    assert result.exit_code == 0


def test_editing_an_already_indexed_file_does_not_trip_the_signal(indexed_clean_project):
    """Precision guard.

    A modified file IS scanned -- the scan loop reads it from disk, not from the
    DB -- so treating any dirty tree as a blind spot would fire constantly and
    be tuned out. Only files absent from the index count.
    """
    (indexed_clean_project / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")

    summary = _summary(indexed_clean_project)
    assert summary["files_unindexed"] == 0
    assert summary["scan_incomplete"] is False


def test_editing_an_indexed_file_to_add_a_credential_is_still_detected(indexed_clean_project):
    """The other half of precision: modified content must still be read."""
    (indexed_clean_project / "app.py").write_text(_LEAK_SOURCE, encoding="utf-8")

    summary = _summary(indexed_clean_project)
    assert summary["total_findings"] >= 1
