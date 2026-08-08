"""100% over zero symbols is not a measurement, and a gate may not pass it.

``_compute_coverage`` hard-coded ``if total <= 0: return 0, 0, 100.0`` -- an
empty denominator became the exact value that means perfect, and
``--threshold`` then compared 100.0 against the caller's bar and passed.

Measured against the tree that shipped, in a repo of real, indexed Python
source where every symbol is underscore-prefixed (so the export-marker
criterion selects none of them)::

    $ roam docs-coverage --threshold 95
      Public symbols: 0
      Documented: 0
      Coverage: 100.0%                                             rc 0
    $ roam --json docs-coverage --threshold 95
      "coverage_pct": 100.0, "gate_passed": true,
      "partial_success": false, "public_symbols": 0, "threshold": 95,
      "verdict": "100.0% doc coverage (0/0 public symbols)"         rc 0

``--help`` promises "Fail with exit code 5 if coverage % is below
threshold". The sibling gate in the same directory on the same index
already refuses::

    $ roam py-types --ci --min-coverage 95
      GATE FAILED: type coverage not computable
      (no_public_python_functions) — required 95%                   rc 5

Both channels agreed on the wrong answer, so the differential sweep's Law 1
(exit-code parity across channels) could not see this. And the pair was
never probed at all: ``tests/test_gate_channel_exit_parity.py`` enumerates
BOOLEAN flags only -- its own docstring says so -- and ``--threshold`` takes
a value.

WHAT IS NOT COVERED HERE
------------------------
Extending that harness to value-bearing threshold options is NOT done. It
needs a per-option decision about which value is "maximally demanding"
(100 for a coverage floor, 0 for a violation ceiling), and guessing the
direction would produce a harness that fails on healthy commands. The axis
stays unmeasured and is recorded as such rather than papered over.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roam.commands.cmd_docs_coverage import _compute_coverage


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1"),
    )


def _indexed(root: Path, source: str) -> Path:
    """A committed, indexed repo holding exactly *source* as src/mod.py."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "mod.py").write_text(source, encoding="utf-8")
    _git(root, "add", "src/mod.py")
    _git(root, "commit", "-q", "-m", "fixture")
    built = _run(root, "init")
    assert built.returncode == 0, built.stdout[-800:] + built.stderr[-800:]
    return root


#: Real Python, really indexed, but nothing the export-marker criterion
#: counts -- so the denominator is 0 while the corpus is not empty.
PRIVATE_ONLY = "def _helper(x):\n    return x + 1\n\n\ndef _other(y):\n    return _helper(y)\n"

#: The control: same shape, public names, one of them documented.
PUBLIC_MIXED = 'def helper(x):\n    """Add one."""\n    return x + 1\n\n\ndef other(y):\n    return helper(y)\n'


@pytest.fixture()
def private_only_repo(tmp_path: Path) -> Path:
    return _indexed(tmp_path / "private_only", PRIVATE_ONLY)


@pytest.fixture()
def public_repo(tmp_path: Path) -> Path:
    return _indexed(tmp_path / "public", PUBLIC_MIXED)


# ---------------------------------------------------------------------------
# The computation itself
# ---------------------------------------------------------------------------


def test_empty_denominator_is_not_a_percentage() -> None:
    """``100.0`` was the return value for "nothing to divide by"."""
    total, documented, pct = _compute_coverage([])
    assert (total, documented) == (0, 0)
    assert pct is None, f"coverage over an empty set must be uncomputable, not {pct!r}"


def test_a_real_denominator_still_produces_a_number() -> None:
    """The control on the same function -- the fix must not swallow real math."""
    symbols = [{"docstring": "doc"}, {"docstring": ""}]
    assert _compute_coverage(symbols) == (2, 1, 50.0)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_threshold_gate_refuses_a_coverage_it_could_not_compute(private_only_repo: Path) -> None:
    run = _run(private_only_repo, "docs-coverage", "--threshold", "95")
    assert run.returncode != 0, (
        "`roam docs-coverage --threshold 95` passed its gate over 0 public "
        f"symbols.\nstdout: {run.stdout.strip()[:600]!r}"
    )
    assert "not computable" in run.stdout, run.stdout[:600]
    assert "100.0%" not in run.stdout, run.stdout[:600]


def test_threshold_gate_refuses_in_the_json_channel_too(private_only_repo: Path) -> None:
    """Both channels agreed on the wrong answer, so pin both on the right one."""
    run = _run(private_only_repo, "--json", "docs-coverage", "--threshold", "95")
    assert run.returncode != 0, run.stdout[:600]
    summary = json.loads(run.stdout[run.stdout.find("{") :])["summary"]
    assert summary["coverage_pct"] is None, summary
    assert summary["coverage_pct_computable"] is False, summary
    assert summary["gate_passed"] is False, summary
    assert summary["partial_success"] is True, summary
    assert summary["state"] == "no_public_symbols", summary


def test_reporting_without_a_threshold_still_exits_zero(private_only_repo: Path) -> None:
    """The narrowness control.

    Nobody asked this run to certify anything, so "this project exports no
    public symbols" is a complete answer. Refusing here would make the
    command useless rather than honest.
    """
    run = _run(private_only_repo, "docs-coverage")
    assert run.returncode == 0, run.stdout[:600] + run.stderr[:600]
    assert "not computable" in run.stdout


def test_a_measurable_repo_still_passes_its_gate(public_repo: Path) -> None:
    """The must-not-fire control: a real 50% still gates on the real number."""
    passing = _run(public_repo, "docs-coverage", "--threshold", "10")
    assert passing.returncode == 0, passing.stdout[:600] + passing.stderr[:600]
    assert "not computable" not in passing.stdout

    failing = _run(public_repo, "--json", "docs-coverage", "--threshold", "99")
    assert failing.returncode != 0
    summary = json.loads(failing.stdout[failing.stdout.find("{") :])["summary"]
    assert summary["coverage_pct_computable"] is True, summary
    assert isinstance(summary["coverage_pct"], (int, float)), summary
    assert summary["partial_success"] is False, summary


def test_uncomputable_and_genuinely_low_are_distinguishable(private_only_repo: Path, public_repo: Path) -> None:
    """Both refuse, and a caller reading the envelope can tell them apart.

    Exit code alone cannot: "I measured 50% and your bar is 99" and "I could
    not measure anything" are different facts that deserve different fixes.
    """
    unknown_out = _run(private_only_repo, "--json", "docs-coverage", "--threshold", "99").stdout
    unknown = json.loads(unknown_out[unknown_out.find("{") :])["summary"]
    low_out = _run(public_repo, "--json", "docs-coverage", "--threshold", "99").stdout
    low = json.loads(low_out[low_out.find("{") :])["summary"]

    assert unknown["gate_passed"] is low["gate_passed"] is False
    assert unknown["coverage_pct_computable"] != low["coverage_pct_computable"]
    assert unknown["verdict"] != low["verdict"]
