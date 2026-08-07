"""W1502 — a documented install target must exist, and UNKNOWN must refuse.

``scripts/sync_surface_counts.py`` proves every install pin names the SAME
version. Nothing proved the version they name can be FETCHED. Those are
different questions with different answers: for 33 commits public ``main``
answered the first green while ``pip install "roam-code==14.0.0"`` returned
404 and ``Cranot/roam-code@v14.0.0`` resolved to no ref, because 14.0.0 had
been declared and never released. The single check that could have seen it
(``verify_release.py --pypi``) ran only from the publish workflow — after the
thing it was meant to prevent had already shipped.

Two properties are pinned here, and the second matters more than the first.

1. **The gate fails on the real defect.** Proven against a synthetic tree
   whose pins name an untagged version, not asserted.

2. **The gate fails CLOSED.** Every way of not knowing — git unusable, no
   tags, PyPI unreachable, PyPI answering 5xx, a scanner that matched nothing
   — must produce UNKNOWN (exit 2) and never OK. "The registry was
   unreachable, so assume the version exists" is the exact defect class this
   repository keeps closing everywhere else; putting it inside the guard
   against that class would be the worst possible place for it. Each of those
   paths gets its own test, because a single collapsed ``except`` is all it
   takes to convert every one of them into a silent pass.

Note the deliberate asymmetry in ``_pypi_has``: only a clean 404 is evidence
of absence. A 500, a timeout, or a proxy interception is an unanswered
question, and ``test_pypi_server_error_is_unknown_not_absent`` is what stops a
future simplification from reading them all as "not published" (which would
fail loudly and wrongly) or as "published" (which would pass silently and
wrongly).
"""

from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scripts import check_install_targets as gate
from scripts import sync_surface_counts as sync

# ---------------------------------------------------------------------------
# The exit-code contract
# ---------------------------------------------------------------------------


def test_the_three_outcomes_are_distinct() -> None:
    """OK / FAIL / UNKNOWN must stay three answers, not two.

    If UNKNOWN ever collapses onto OK the gate becomes decorative, and if it
    collapses onto FAIL it becomes noise that gets disabled. The whole design
    rests on the operator being able to tell "this is broken" from "I could
    not tell".
    """
    assert (gate.OK, gate.FAIL, gate.UNKNOWN) == (0, 1, 2)
    assert len({gate.OK, gate.FAIL, gate.UNKNOWN}) == 3


# ---------------------------------------------------------------------------
# Positive control — the real tree
# ---------------------------------------------------------------------------


def test_real_tree_install_targets_all_exist() -> None:
    """Offline, no network: every install pin in the checkout names a real tag.

    This is the assertion the tree failed for 33 commits with every gate
    green. It runs in the ordinary test suite as well as on the push path, so
    a reintroduction fails locally rather than at a consumer's CI runtime.
    """
    code, report = gate.check(check_pypi=False)
    assert code == gate.OK, report
    assert report["install_pin_sites"] > 0, "the scanner found nothing; that is a broken sweep, not a clean tree"


# ---------------------------------------------------------------------------
# Negative control — the gate fails on the defect it exists for
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A synthetic repo the scanner walks exactly like the real one."""

    def _build(files: dict[str, str], tags: list[str]) -> Path:
        for rel, body in files.items():
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8", newline="")
        monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(sync, "_tracked_files", lambda: sorted(files))
        monkeypatch.setattr(sync, "release_tags", lambda: list(tags))
        return tmp_path

    return _build


_PINS = (
    'python -m pip install --quiet "roam-code==14.0.0"\n'
    "      - uses: Cranot/roam-code@v14.0.0\n"
    "raise SystemExit(0 if actual == '14.0.0' else 1)\n"
)


def test_gate_fails_when_the_pinned_version_has_no_tag(fake_tree) -> None:
    """The measured defect, reproduced: declared 14.0.0, tagged only 13.10.0."""
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v13.9.0", "v13.10.0"])
    code, report = gate.check(check_pypi=False)
    assert code == gate.FAIL, report
    assert report["missing_tag"] == ["14.0.0"]
    assert report["offending_sites"], "a failure that names no site is not actionable"
    assert all(s["path"] == "src/roam/templates/ci/gitlab-ci.yml" for s in report["offending_sites"])


def test_gate_passes_when_the_pinned_version_is_tagged(fake_tree) -> None:
    """The positive half of the same control — otherwise FAIL proves nothing."""
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v13.10.0", "v14.0.0"])
    code, report = gate.check(check_pypi=False)
    assert code == gate.OK, report


# ---------------------------------------------------------------------------
# Fail-closed — every way of not knowing must REFUSE
# ---------------------------------------------------------------------------


def test_unreachable_git_is_unknown_not_ok(fake_tree, monkeypatch) -> None:
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=[])

    def _boom():
        raise SystemExit("git exited 128: not a git repository")

    monkeypatch.setattr(sync, "release_tags", _boom)
    code, report = gate.check(check_pypi=False)
    assert code == gate.UNKNOWN, report
    assert "git" in report["reason"]


def test_empty_tag_list_is_unknown_not_a_clean_tree(fake_tree) -> None:
    """No tags is not "nothing to check" — it is "I cannot check".

    A shallow clone has no tags. Reading that as OK would make the gate a
    no-op on precisely the CI configuration most likely to run it.
    """
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=[])
    code, report = gate.check(check_pypi=False)
    assert code != gate.OK, report


def test_no_install_pins_found_is_unknown_not_ok(fake_tree) -> None:
    """A sweep that matched nothing is a broken scanner, not a clean tree.

    This repository ships seven CI templates that each carry an install pin.
    If a future refactor breaks the patterns, the gate must say so rather than
    congratulate itself on a tree it never read.
    """
    fake_tree({"README.md": "roam-code has no pins here\n"}, tags=["v13.10.0"])
    code, report = gate.check(check_pypi=False)
    assert code == gate.UNKNOWN, report
    assert report["install_pin_sites"] == 0


def test_unreachable_pypi_is_unknown_not_ok(fake_tree, monkeypatch) -> None:
    """The headline fail-closed case, and the easiest one to get wrong.

    ``verify_release.py::_pypi_latest`` returns ``None`` on a URLError and its
    caller turns that into a failure — fine there. Here the tempting shape is
    "could not reach PyPI, tag existed, call it OK", which would render an
    unreachable registry as "the version exists".
    """
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])

    def _unreachable(*_a, **_kw):
        raise urllib.error.URLError("getaddrinfo failed")

    monkeypatch.setattr(urllib.request, "urlopen", _unreachable)
    code, report = gate.check(check_pypi=True)
    assert code == gate.UNKNOWN, report
    assert "unreachable" in report["reason"].lower()


def test_pypi_server_error_is_unknown_not_absent(fake_tree, monkeypatch) -> None:
    """A 5xx is an unanswered question, not evidence the wheel is missing."""
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])

    def _five_hundred(*_a, **_kw):
        raise urllib.error.HTTPError("url", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _five_hundred)
    code, report = gate.check(check_pypi=True)
    assert code == gate.UNKNOWN, report
    assert "503" in report["reason"]


def test_pypi_clean_404_is_a_real_failure(fake_tree, monkeypatch) -> None:
    """The one HTTP outcome that IS evidence of absence.

    Tag present, wheel absent — the state a tag alone cannot detect, and the
    reason ``--pypi`` exists at all.
    """
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])

    def _not_found(*_a, **_kw):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _not_found)
    code, report = gate.check(check_pypi=True)
    assert code == gate.FAIL, report
    assert report["missing_pypi"] == ["14.0.0"]
    assert report["missing_tag"] == [], "the tag exists; only PyPI is missing the wheel"


# ---------------------------------------------------------------------------
# Wiring — a gate nothing runs is not a gate
# ---------------------------------------------------------------------------


def test_gate_runs_on_the_push_path_and_in_ci() -> None:
    """The whole point: this must NOT repeat verify_release.py's mistake.

    ``verify_release.py --pypi`` would have caught the defect and exits 1
    today, but it is invoked only from ``publish.yml`` — after publication.
    A truth check that runs after the lie has shipped is not a gate. This one
    is offline and sub-second, so it belongs where the lie is created: the
    push path and ordinary CI.
    """
    root = Path(__file__).resolve().parents[1]
    prepush = (root / "scripts" / "prepush_check.py").read_text(encoding="utf-8")
    ci = (root / ".github" / "workflows" / "roam-ci.yml").read_text(encoding="utf-8")
    assert "check_install_targets.py" in prepush, "the pre-push gate does not run the install-target check"
    assert "check_install_targets.py" in ci, "CI does not run the install-target check"


def test_gate_is_offline_and_fast() -> None:
    """No network in the default mode, so it can live on the push path.

    Asserted by running the real script as a subprocess with ``--json``: if it
    ever grew a default network call this would either hang or fail in an
    offline environment, which is where contributors actually run pre-push.
    """
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "check_install_targets.py"), "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == gate.OK, proc.stdout + proc.stderr
    assert '"status": "OK"' in proc.stdout
    assert '"checked_pypi": false' in proc.stdout
