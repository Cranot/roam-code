"""An unrunnable dirty-tree probe is an absent answer, not a clean tree.

``_git_dirty_hash`` used to return ``None`` for three different facts: the tree
is clean, git is not installed, the path is not a repository (and, via the
10-second timeout, "``git status`` was too slow"). Only the first is an
observation. The other two are the absence of one, and flattening them onto the
same value let every reader downstream treat "not inspected" as "inspected and
clean".

Measured on HEAD before the fix, with ``project_root`` pointed at a directory
that is not a git repository::

    _git_dirty_hash(non_git_dir)                       -> None

    predicate signed DIRTY  + probe unavailable
        state  : mismatch
        error  : git_dirty_hash mismatch - predicate was signed against a
                 dirty tree, but the live working tree is clean now

    predicate signed CLEAN  + probe unavailable
        state  : verified
        errors : []

Both are wrong, and they are the same defect seen from its two ends:

* The first is the inversion. "The live working tree is clean now" is a
  positive claim about a tree the verifier never looked at, and ``mismatch``
  is the channel reserved for evidence that moved -- so a missing git binary
  in a CI image renders byte-identically to someone editing the attestation.
  A tamper alarm that fires on toolchain gaps is one people learn to skip.
* The second is the fail-open original. ``None == None`` compared equal, so a
  CGA asserting a clean tree came back VERIFIED on a box where the tree was
  never examined -- and it would have come back VERIFIED with the tree in any
  state whatsoever, because nothing read it.

The emit gate had the third instance: ``if dirty is not None: refuse`` passed
an unknown straight through and signed a predicate asserting a clean tree.

The fix gives the probe a third value, ``DIRTY_HASH_UNKNOWN``, and a fourth
verification state, ``unverifiable``, that is neither a pass nor an accusation.

Every positive assertion here carries a negative control in the same file, so
a change that merely makes the verifier refuse everything cannot pass: a real
clean tree must still verify, and a real dirty-tree mismatch must still reach
``mismatch`` with its tamper wording intact.
"""

from __future__ import annotations

import sqlite3
import subprocess

import pytest

from roam.attest.cga import (
    DIRTY_HASH_UNKNOWN,
    PREDICATE_TYPE,
    STATEMENT_TYPE,
    VERIFY_STATE_MISMATCH,
    VERIFY_STATE_UNVERIFIABLE,
    VERIFY_STATE_VERIFIED,
    _git_dirty_hash,
    build_cga_predicate,
    classify_verification_state,
    verify_cga_statement,
    verify_cga_statement_state,
)

_SCHEMA = """
CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, language TEXT);
CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, qualified_name TEXT,
                      name TEXT, kind TEXT, signature TEXT);
CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER,
                    target_id INTEGER, kind TEXT);
CREATE TABLE index_manifest (id INTEGER PRIMARY KEY, schema_version INTEGER,
                             parser_versions TEXT, grammar_versions TEXT,
                             component_versions TEXT);
INSERT INTO files (id, path, language) VALUES (1, 'a.py', 'python');
INSERT INTO symbols (id, file_id, qualified_name, name, kind, signature)
    VALUES (1, 1, 'a.f', 'f', 'function', '()');
INSERT INTO edges (source_id, target_id, kind) VALUES (1, 1, 'calls');
INSERT INTO index_manifest (id, schema_version, parser_versions, grammar_versions,
                            component_versions)
    VALUES (1, 7, '{}', '{}', '{}');
"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_SCHEMA)
    yield c
    c.close()


def _statement(conn, root, dirty_hash):
    """A statement whose graph fingerprints already agree with *conn*.

    Isolates the dirty-tree binding: any error the verify reports is about
    the working tree, never about the digests.
    """
    predicate = build_cga_predicate(conn, project_root=root, tool_version="test", dirty_hash=dirty_hash)
    return {
        "_type": STATEMENT_TYPE,
        "predicateType": PREDICATE_TYPE,
        # "unknown" subject SHA is the documented skip for the commit check,
        # so the commit binding cannot contribute an error here.
        "subject": [{"name": "p", "digest": {"git_commit_sha1": "unknown"}}],
        "predicate": predicate,
    }


def _git_repo(path):
    """Init a real repo, or skip. Nothing here simulates git."""
    if not _have_git():
        pytest.skip("git binary not available")
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    for key, val in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(path), "config", key, val], check=True, capture_output=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "seed.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "seed"], check=True, capture_output=True)
    return path


def _have_git():
    try:
        return subprocess.run(["git", "--version"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------
# The probe itself: three facts, three values.
# --------------------------------------------------------------------------


def test_probe_returns_unknown_when_it_cannot_run(tmp_path):
    """Not a repository -> UNKNOWN, which is not the clean-tree value."""
    assert _git_dirty_hash(tmp_path) == DIRTY_HASH_UNKNOWN
    assert _git_dirty_hash(tmp_path) is not None


def test_probe_returns_none_only_for_an_observed_clean_tree(tmp_path):
    """Negative control: a real clean repo still reports clean, not unknown."""
    root = _git_repo(tmp_path / "repo")
    assert _git_dirty_hash(root) is None


def test_probe_returns_a_digest_for_a_real_dirty_tree(tmp_path):
    """Negative control: a real dirty repo still reports a 64-char digest."""
    root = _git_repo(tmp_path / "repo")
    (root / "untracked.txt").write_text("x\n", encoding="utf-8")
    got = _git_dirty_hash(root)
    assert got not in (None, DIRTY_HASH_UNKNOWN)
    assert len(got) == 64


# --------------------------------------------------------------------------
# Verify: the unknown must not read as tampering, and must not read as a pass.
# --------------------------------------------------------------------------


def test_unknown_live_probe_is_not_reported_as_tampering(conn, tmp_path):
    """Predicate signed dirty + probe unavailable.

    Pre-fix this was ``mismatch`` carrying "the live working tree is clean
    now". Both halves were unearned: the state accused, and the sentence
    asserted a fact about an uninspected tree.
    """
    stmt = _statement(conn, tmp_path, dirty_hash="ab" * 32)
    state, errors = verify_cga_statement_state(stmt, conn, project_root=tmp_path)

    assert state == VERIFY_STATE_UNVERIFIABLE, errors
    assert state != VERIFY_STATE_MISMATCH
    joined = " ".join(errors)
    assert "environment_unknown" in joined
    # The specific fabrication that used to appear here.
    assert "clean now" not in joined
    assert "NOT evidence of tampering" in joined


def test_unknown_live_probe_does_not_verify_a_clean_claim(conn, tmp_path):
    """Predicate asserts clean + probe unavailable.

    Pre-fix: ``None == None`` -> no error -> VERIFIED. The statement's clean
    claim was confirmed by a check that never ran.
    """
    stmt = _statement(conn, tmp_path, dirty_hash=None)
    state, errors = verify_cga_statement_state(stmt, conn, project_root=tmp_path)

    assert state != VERIFY_STATE_VERIFIED
    assert state == VERIFY_STATE_UNVERIFIABLE, errors
    # Fails closed on the boolean front door too, not only on the state one.
    ok, _ = verify_cga_statement(stmt, conn, project_root=tmp_path)
    assert ok is False


def test_two_unknowns_do_not_cancel_into_a_match(conn, tmp_path):
    """Signer unknown + verifier unknown.

    ``"unknown" == "unknown"`` is two absent answers agreeing about nothing.
    Equality must not be consulted before the unknown check.
    """
    stmt = _statement(conn, tmp_path, dirty_hash=DIRTY_HASH_UNKNOWN)
    state, errors = verify_cga_statement_state(stmt, conn, project_root=tmp_path)

    assert state == VERIFY_STATE_UNVERIFIABLE, errors
    assert "neither the signer nor this verifier" in " ".join(errors)


def test_real_clean_tree_still_verifies(conn, tmp_path):
    """Negative control: the fix must not refuse a tree it CAN inspect."""
    root = _git_repo(tmp_path / "repo")
    stmt = _statement(conn, root, dirty_hash=None)
    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)

    assert state == VERIFY_STATE_VERIFIED, errors
    assert errors == []


def test_real_dirty_tree_still_reports_a_hard_mismatch(conn, tmp_path):
    """Negative control: the tamper channel keeps its wording and its state.

    A predicate asserting clean, verified against a tree that git can read and
    reports as dirty, is a genuine discrepancy. It must stay MISMATCH -- the
    new soft state must not swallow the alarm it was carved out to protect.
    """
    root = _git_repo(tmp_path / "repo")
    stmt = _statement(conn, root, dirty_hash=None)
    (root / "untracked.txt").write_text("x\n", encoding="utf-8")

    state, errors = verify_cga_statement_state(stmt, conn, project_root=root)
    assert state == VERIFY_STATE_MISMATCH, errors
    assert "git_dirty_hash mismatch" in " ".join(errors)


# --------------------------------------------------------------------------
# The state lattice.
# --------------------------------------------------------------------------


def test_a_definite_discrepancy_outranks_any_unknown():
    """One hard error re-asserts MISMATCH however many unknowns sit beside it.

    Without this, an unknown would be a laundering channel: pair it with a
    wrong-commit error and the verdict softens.
    """
    errors = [
        "environment_unknown: git_dirty_hash could not be established",
        "git_commit_sha1 mismatch - statement signed against abc, live tree is at def",
    ]
    assert classify_verification_state(errors) == VERIFY_STATE_MISMATCH


def test_unknown_alone_classifies_as_unverifiable():
    assert (
        classify_verification_state(["environment_unknown: git_dirty_hash could not be established"])
        == VERIFY_STATE_UNVERIFIABLE
    )


def test_no_errors_still_means_verified():
    """Negative control on the lattice: the pass path is untouched."""
    assert classify_verification_state([]) == VERIFY_STATE_VERIFIED


# --------------------------------------------------------------------------
# Emit: the gate must not certify a tree it could not read.
# --------------------------------------------------------------------------


def test_emit_gate_refuses_when_the_tree_state_is_unknown(tmp_path, monkeypatch):
    """``roam cga emit`` without --allow-dirty, probe unavailable.

    Pre-fix the gate read ``None`` as clean and emitted a predicate asserting
    a clean tree. The refusal must name the real cause: an operator sent to
    ``git stash`` for a missing git binary is a wasted hour.
    """
    from click.testing import CliRunner

    from roam.commands.cmd_cga import cga_emit

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cga_emit, ["--output", str(tmp_path / "cga.json")])

    assert result.exit_code != 0
    combined = f"{result.output}{result.exception}"
    assert "DIRTY_TREE" in combined
    assert "could not determine" in combined
    assert not (tmp_path / "cga.json").exists()
