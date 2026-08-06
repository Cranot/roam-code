"""``roam mutate`` must never write to disk when ``--dry-run`` was asked for.

``mutate move`` and ``mutate rename`` both *declare* a ``--dry-run`` flag
and both *bind* it into the command signature — and neither body ever read
it. The write decision was recomputed from ``--apply`` alone::

    move_symbol(conn, sym, target, dry_run=(not apply_changes))

With no flags the default is already a preview, so nothing looked wrong.
But ``roam mutate rename FOO BAR --apply --dry-run`` set
``apply_changes=True``, computed ``dry_run=False``, and rewrote source
files on disk while the user had explicitly asked for a preview. That is
irreversible, so ambiguous intent must not be resolved silently.

The contract asserted here:

1. ``--apply --dry-run`` together is a ``UsageError`` (exit 2), not a
   silent pick, for both ``move`` and ``rename``.
2. No bytes on disk change when both flags are passed.
3. ``--dry-run`` alone still previews (exit 0, no writes) — the flag is
   genuinely read, not merely rejected in combination.
4. ``--apply`` alone still writes — the guard did not disarm the
   destructive path.

Byte-level evidence: every file in the fixture is hashed (sha256) before
and after, so a write anywhere in the tree fails the test, not just a
write to the one file we thought to look at.
"""

from __future__ import annotations

import hashlib
import os
import subprocess

import pytest
from click.testing import CliRunner

from tests.conftest import index_in_process, invoke_cli


def _git_init_commit(path):
    """Init + commit so roam's git-aware indexer sees the files."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(path),
        capture_output=True,
        env=env,
    )


def _hash_tree(root):
    """sha256 every tracked ``.py`` file, keyed by name.

    ``.roam/`` is skipped — the index db legitimately changes.
    """
    out = {}
    for p in sorted(root.rglob("*.py")):
        if ".roam" in p.parts or ".git" in p.parts:
            continue
        out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def dry_run_project(tmp_path):
    """Two-file fixture: ``greet`` defined in ``svc.py``, called from
    ``caller.py``. Small enough that any write is unambiguous.
    """
    proj = tmp_path / "dry_run_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "svc.py").write_text('def greet(name):\n    return "hello " + name\n')
    (proj / "caller.py").write_text("from svc import greet\n\ndef c():\n    return greet('C')\n")
    _git_init_commit(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# 1 + 2. --apply --dry-run is refused, and nothing is written
# ---------------------------------------------------------------------------


def test_rename_apply_plus_dry_run_writes_nothing(dry_run_project):
    """``mutate rename --apply --dry-run`` must not touch the disk."""
    before = _hash_tree(dry_run_project)
    result = invoke_cli(
        CliRunner(),
        ["mutate", "rename", "greet", "salutate", "--apply", "--dry-run"],
        cwd=dry_run_project,
    )
    after = _hash_tree(dry_run_project)

    changed = sorted(k for k in before if before[k] != after.get(k))
    assert changed == [], (
        f"--dry-run was passed explicitly yet these files were rewritten: {changed}\n"
        f"exit_code={result.exit_code}\noutput:\n{result.output}"
    )


def test_move_apply_plus_dry_run_writes_nothing(dry_run_project):
    """``mutate move --apply --dry-run`` must not touch the disk."""
    before = _hash_tree(dry_run_project)
    result = invoke_cli(
        CliRunner(),
        ["mutate", "move", "greet", "moved.py", "--apply", "--dry-run"],
        cwd=dry_run_project,
    )
    after = _hash_tree(dry_run_project)

    changed = sorted(k for k in before if before[k] != after.get(k))
    created = sorted(k for k in after if k not in before)
    assert changed == [] and created == [], (
        f"--dry-run was passed explicitly yet the tree changed "
        f"(rewritten={changed}, created={created})\n"
        f"exit_code={result.exit_code}\noutput:\n{result.output}"
    )


def test_rename_apply_plus_dry_run_is_a_usage_error(dry_run_project):
    """Ambiguous intent is refused loudly (exit 2), never resolved silently."""
    result = invoke_cli(
        CliRunner(),
        ["mutate", "rename", "greet", "salutate", "--apply", "--dry-run"],
        cwd=dry_run_project,
    )
    assert result.exit_code == 2, (
        f"expected exit 2 (UsageError) for --apply --dry-run, got {result.exit_code}\n{result.output}"
    )
    assert "mutually exclusive" in result.output, result.output


def test_move_apply_plus_dry_run_is_a_usage_error(dry_run_project):
    """Same refusal for ``move``."""
    result = invoke_cli(
        CliRunner(),
        ["mutate", "move", "greet", "moved.py", "--apply", "--dry-run"],
        cwd=dry_run_project,
    )
    assert result.exit_code == 2, (
        f"expected exit 2 (UsageError) for --apply --dry-run, got {result.exit_code}\n{result.output}"
    )
    assert "mutually exclusive" in result.output, result.output


# ---------------------------------------------------------------------------
# 3. --dry-run alone still previews (the flag is read, not just rejected)
# ---------------------------------------------------------------------------


def test_rename_dry_run_alone_previews_without_writing(dry_run_project):
    before = _hash_tree(dry_run_project)
    result = invoke_cli(
        CliRunner(),
        ["mutate", "rename", "greet", "salutate", "--dry-run"],
        cwd=dry_run_project,
    )
    after = _hash_tree(dry_run_project)

    assert result.exit_code == 0, f"--dry-run alone should succeed:\n{result.output}"
    assert before == after, "--dry-run alone must not write"


def test_move_dry_run_alone_previews_without_writing(dry_run_project):
    before = _hash_tree(dry_run_project)
    result = invoke_cli(
        CliRunner(),
        ["mutate", "move", "greet", "moved.py", "--dry-run"],
        cwd=dry_run_project,
    )
    after = _hash_tree(dry_run_project)

    assert result.exit_code == 0, f"--dry-run alone should succeed:\n{result.output}"
    assert before == after, "--dry-run alone must not write"
    assert not (dry_run_project / "moved.py").exists()


# ---------------------------------------------------------------------------
# 4. --apply alone still writes (the guard did not disarm the real path)
# ---------------------------------------------------------------------------


def test_rename_apply_alone_still_writes(dry_run_project):
    before = _hash_tree(dry_run_project)
    result = invoke_cli(
        CliRunner(),
        ["mutate", "rename", "greet", "salutate", "--apply"],
        cwd=dry_run_project,
    )
    after = _hash_tree(dry_run_project)

    assert result.exit_code == 0, result.output
    assert before != after, f"--apply alone must still write:\n{result.output}"
    assert "def salutate(name):" in (dry_run_project / "svc.py").read_text()
