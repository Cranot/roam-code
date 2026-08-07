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

2. **The gate fails CLOSED.** Every way of not knowing — git unusable, an
   EMPTY tag list, a tracked file that would not open, a scanner that matched
   nothing, a shipped template with no pin at all, a registry that did not
   answer, and a registry answer that is not the registry's own JSON for the
   version asked about — must produce UNKNOWN (exit 2) and never OK. "The
   registry was unreachable, so assume the version exists" is the exact defect
   class this repository keeps closing everywhere else; putting it inside the
   guard against that class would be the worst possible place for it. Each of
   those paths gets its own test, because a single collapsed ``except`` is all
   it takes to convert every one of them into a silent pass.

Note the deliberate asymmetry in ``_pypi_has``: only a clean 404 is evidence
of absence. A 500, a timeout, or a proxy interception is an unanswered
question, and ``test_pypi_server_error_is_unknown_not_absent`` is what stops a
future simplification from reading them all as "not published" (which would
fail loudly and wrongly) or as "published" (which would pass silently and
wrongly).

**Two of the tests below exist because the first version of this module
proved less than it claimed, and both failures were in the ASSERTION, not in
the code under test.** ``test_empty_tag_list_is_unknown_not_a_clean_tree``
asserted ``!= OK`` where every sibling asserted ``== UNKNOWN``; FAIL satisfies
``!= OK``, so the one path whose implementation returned FAIL was the one path
whose test accepted it, and twelve green tests coexisted with a docstring and
a CHANGELOG entry describing an outcome the code never produced. And the
``--pypi`` tests covered URLError, 503 and 404 while the whole 2xx quadrant —
where an unreachable registry actually LOOKS like success — had no test at
all. An assertion written to what the code does can never contradict what the
code does; write it to the claim.
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
from tests._helpers.repo_root import repo_root

ROOT = repo_root()

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
    code, report = gate.check(check_pypi=False, check_remote=False)
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
    code, report = gate.check(check_pypi=False, check_remote=False)
    assert code == gate.FAIL, report
    assert report["missing_tag"] == ["14.0.0"]
    assert report["offending_sites"], "a failure that names no site is not actionable"
    assert all(s["path"] == "src/roam/templates/ci/gitlab-ci.yml" for s in report["offending_sites"])


def test_gate_passes_when_the_pinned_version_is_tagged(fake_tree) -> None:
    """The positive half of the same control — otherwise FAIL proves nothing."""
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v13.10.0", "v14.0.0"])
    code, report = gate.check(check_pypi=False, check_remote=False)
    assert code == gate.OK, report


# ---------------------------------------------------------------------------
# Fail-closed — every way of not knowing must REFUSE
# ---------------------------------------------------------------------------


def test_unreachable_git_is_unknown_not_ok(fake_tree, monkeypatch) -> None:
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=[])

    def _boom():
        raise SystemExit("git exited 128: not a git repository")

    monkeypatch.setattr(sync, "release_tags", _boom)
    code, report = gate.check(check_pypi=False, check_remote=False)
    assert code == gate.UNKNOWN, report
    assert "git" in report["reason"]


def test_empty_tag_list_is_unknown_not_a_clean_tree(fake_tree) -> None:
    """No tags is not "nothing to check" — it is "I cannot check".

    A shallow clone has no tags. Reading that as OK would make the gate a
    no-op on precisely the CI configuration most likely to run it.

    The assertion is ``== UNKNOWN`` and not ``!= OK`` on purpose, and the
    difference is the whole test. ``!= OK`` is also satisfied by FAIL, which
    is what this path actually did when it shipped: the empty set flowed into
    the membership test, every pinned version came back missing, and a plain
    ``git clone --depth 1`` was told that 13.10.0 "does not exist" — a false
    assertion of ABSENCE manufactured from missing data, which is the same
    defect as the false assertion of presence with the sign flipped. An
    assertion written to the observed behaviour instead of to the claimed
    contract is how the gate's own docstring and CHANGELOG entry came to
    describe an outcome the code never produced.
    """
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=[])
    code, report = gate.check(check_pypi=False, check_remote=False)
    assert code == gate.UNKNOWN, report
    assert "fetch --tags" in report["reason"], "the refusal must name the remedy that actually works"


def test_empty_tag_list_refusal_survives_the_cli(fake_tree) -> None:
    """Exit 2 at the process boundary, since that is what the hook reads."""
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=[])
    assert gate.main([]) == gate.UNKNOWN


def test_no_install_pins_found_is_unknown_not_ok(fake_tree) -> None:
    """A sweep that matched nothing is a broken scanner, not a clean tree.

    This repository ships seven CI templates that each carry an install pin.
    If a future refactor breaks the patterns, the gate must say so rather than
    congratulate itself on a tree it never read.
    """
    fake_tree({"README.md": "roam-code has no pins here\n"}, tags=["v13.10.0"])
    code, report = gate.check(check_pypi=False, check_remote=False)
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
    code, report = gate.check(check_pypi=True, check_remote=False)
    assert code == gate.UNKNOWN, report
    assert "unreachable" in report["reason"].lower()


def test_pypi_server_error_is_unknown_not_absent(fake_tree, monkeypatch) -> None:
    """A 5xx is an unanswered question, not evidence the wheel is missing."""
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])

    def _five_hundred(*_a, **_kw):
        raise urllib.error.HTTPError("url", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _five_hundred)
    code, report = gate.check(check_pypi=True, check_remote=False)
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
    code, report = gate.check(check_pypi=True, check_remote=False)
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
    root = ROOT
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
    root = ROOT
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


# ---------------------------------------------------------------------------
# Fail-closed, part two: a 2xx is not an answer
# ---------------------------------------------------------------------------
#
# The first version of this module tested URLError, 503 and 404 and stopped.
# That leaves the whole 2xx quadrant untested, and the 2xx quadrant is where
# "the registry was unreachable" actually LOOKS like success: a captive
# portal, a corporate MITM proxy with a trusted root, a cache. Every one of
# them answers 200, and ``urllib``'s default opener honours ``https_proxy``
# from the environment. Measured against the pre-fix gate, all seven shapes
# below returned exit 0 -- the gate certified that an unpublished version
# existed, which is the exact defect class it was written to prevent,
# reproduced inside the guard against it.


class _FakeResponse:
    """Enough of an ``http.client.HTTPResponse`` for ``_fetch_json``."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"{}",
        content_type: str = "application/json",
        url: str | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self._content_type = content_type
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def geturl(self) -> str:
        return self._url or gate.PYPI_URL.format(version="14.0.0")

    @property
    def headers(self):
        ct = self._content_type

        class _H:
            @staticmethod
            def get_content_type() -> str:
                return ct

        return _H()

    def read(self, _n: int = -1) -> bytes:
        return self._body


def _answering(response: _FakeResponse):
    def _urlopen(*_a, **_kw):
        return response

    return _urlopen


_GOOD_PYPI = b'{"info": {"version": "14.0.0"}}'


@pytest.mark.parametrize(
    "label,response",
    [
        ("empty body", _FakeResponse(body=b"")),
        ("captive-portal HTML", _FakeResponse(body=b"<html>Sign in</html>", content_type="text/html")),
        ("malformed JSON", _FakeResponse(body=b"{not json")),
        ("JSON that is not an object", _FakeResponse(body=b"[1, 2, 3]")),
        ("204 No Content", _FakeResponse(status=204, body=b"")),
        ("redirected to another host", _FakeResponse(body=_GOOD_PYPI, url="https://portal.example/login")),
        ("JSON about a different version", _FakeResponse(body=b'{"info": {"version": "13.10.0"}}')),
    ],
)
def test_a_2xx_that_is_not_the_registrys_answer_is_unknown(fake_tree, monkeypatch, label, response) -> None:
    """Seven ways to answer 200 without answering the question.

    The tag exists in this tree, so ONLY the PyPI half can decide the result:
    an exit 0 here means the HTTP response was read as "the wheel is
    published". None of these responses says that.
    """
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])
    monkeypatch.setattr(urllib.request, "urlopen", _answering(response))
    code, report = gate.check(check_pypi=True, check_remote=False)
    assert code == gate.UNKNOWN, f"{label}: {report}"


def test_the_positive_control_still_passes(fake_tree, monkeypatch) -> None:
    """Without this, the seven refusals above prove only that nothing passes."""
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])
    monkeypatch.setattr(urllib.request, "urlopen", _answering(_FakeResponse(body=_GOOD_PYPI)))
    code, report = gate.check(check_pypi=True, check_remote=False)
    assert code == gate.OK, report


# ---------------------------------------------------------------------------
# The remote half -- a local tag is not a tag anyone else can resolve
# ---------------------------------------------------------------------------


def test_a_locally_tagged_but_unpushed_release_fails_the_remote_check(fake_tree, monkeypatch) -> None:
    """``git tag v14.0.0`` satisfies the offline check and nobody else.

    This is the release path, not a hypothetical: the tag is created locally
    first, and between creation and push every consumer's
    ``Cranot/roam-code@v14.0.0`` resolves to nothing while the offline gate
    reports OK.
    """
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])

    def _not_found(*_a, **_kw):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _not_found)
    offline_code, _ = gate.check(check_pypi=False, check_remote=False)
    assert offline_code == gate.OK, "the offline check cannot see this, which is why --remote exists"

    code, report = gate.check(check_pypi=False, check_remote=True)
    assert code == gate.FAIL, report
    assert report["missing_remote"] == ["14.0.0"]


def test_remote_check_refuses_when_github_does_not_answer(fake_tree, monkeypatch) -> None:
    """Rate-limited or intercepted is not "the tag is there"."""
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])

    def _rate_limited(*_a, **_kw):
        raise urllib.error.HTTPError("url", 403, "rate limit exceeded", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _rate_limited)
    code, report = gate.check(check_pypi=False, check_remote=True)
    assert code == gate.UNKNOWN, report


# ---------------------------------------------------------------------------
# A pin that does not exist is invisible to a pin scanner
# ---------------------------------------------------------------------------


def test_a_shipped_template_with_no_pin_at_all_is_unknown(fake_tree) -> None:
    """The hole a pin scanner cannot see by construction.

    ``pip install roam-code`` produces no site: nothing to rewrite, nothing to
    check, and nothing in the count. The gate reported OK about a file it had
    never evaluated, and that file's install target was "whatever is latest
    when this runs" -- not a target anyone can check. Shipped that way in
    ``templates/examples/roam-guard-pr.jenkinsfile``, the only member of a
    five-template family without a pin, while its README described all five
    as equivalent drop-ins.
    """
    fake_tree(
        {
            "src/roam/templates/ci/gitlab-ci.yml": _PINS,
            "templates/examples/roam-guard-pr.jenkinsfile": "sh 'pip install --quiet roam-code'\n",
        },
        tags=["v14.0.0"],
    )
    code, report = gate.check(check_pypi=False, check_remote=False)
    assert code == gate.UNKNOWN, report
    assert report["unpinned_shipped_surfaces"] == ["templates/examples/roam-guard-pr.jenkinsfile"]


def test_the_real_shipped_templates_are_all_pinned() -> None:
    """Positive control on the real tree, and a floor on the family's size."""
    surfaces = sync.shipped_install_surfaces()
    assert len(surfaces) >= 8, surfaces
    sites, _ = sync.install_pin_scan()
    assert gate._unpinned_shipped_surfaces(sites) == []


# ---------------------------------------------------------------------------
# The denominator -- a numerator alone is not a measurement
# ---------------------------------------------------------------------------


def test_an_unreadable_tracked_file_is_unknown_not_zero_pins(fake_tree, monkeypatch) -> None:
    """A file that would not open may hold the pin nobody checked.

    The scanner used to ``continue`` past ``OSError`` and past undecodable
    bytes without counting either, so the report was a numerator with no
    denominator and a bad pin in such a file produced exit 0.
    """
    fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v14.0.0"])
    monkeypatch.setattr(sync, "_tracked_files", lambda: ["src/roam/templates/ci/gitlab-ci.yml", "vanished.yml"])
    code, report = gate.check(check_pypi=False, check_remote=False)
    assert code == gate.UNKNOWN, report
    assert report["scan"]["unreadable"] == 1


def test_a_pin_in_a_non_utf8_file_is_still_seen(fake_tree, monkeypatch) -> None:
    """Undecodable bytes must not hide the ASCII bytes beside them.

    A pin is ASCII. Skipping the whole file on ``UnicodeDecodeError`` dropped
    it; decoding with replacement keeps every ASCII byte and turns only the
    undecodable ones into U+FFFD, which matches no pattern.
    """
    root = fake_tree({"src/roam/templates/ci/gitlab-ci.yml": _PINS}, tags=["v13.10.0"])
    (root / "odd.yml").write_bytes(b'\xff\xfe pip install "roam-code==14.0.0"\n')
    monkeypatch.setattr(sync, "_tracked_files", lambda: ["src/roam/templates/ci/gitlab-ci.yml", "odd.yml"])
    sites, scan = sync.install_pin_scan()
    assert scan["non_utf8"] == 1
    assert any(rel == "odd.yml" for rel, _, _ in sites), "the pin in the undecodable file was never seen"


def test_the_report_carries_the_denominator() -> None:
    """ "44 pins are fine" says nothing about the files that were skipped."""
    _, report = gate.check(check_pypi=False, check_remote=False)
    scan = report["scan"]
    assert scan["tracked"] == scan["scanned"] + scan["exempt"] + scan["no_pin_token"] + scan["unreadable"]
    assert scan["scanned"] > 0
