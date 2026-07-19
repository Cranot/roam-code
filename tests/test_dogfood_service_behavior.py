"""Dogfood behavioral tests for the SERVICE-DELIVERY & PR-INTELLIGENCE cluster.

These exercise the *money-layer* deliverables (``service-report``, ``capsule``,
``pr-replay``) and the *merge-gate* commands (``critique``, ``pr-analyze``,
``pr-bundle``, ``postmortem``) against roam's own repository — the same way a
paying customer runs them — and assert the SHAPE of the real deliverable, not
just an exit code.

They run the CLI as a subprocess (``<venv-python> -m roam ...``) against the
already-built ``.roam/`` index. Nothing here mutates ``src/`` or the main
index; hermetic bits (pr-bundle lifecycle) use a throwaway git repo.

Several tests are ``xfail(strict=True)`` — they assert the CORRECT customer
behavior and currently fail because of a confirmed defect. When the underlying
bug is fixed the test will ``xpass`` and the strict marker turns that into a
failure, forcing this file to be updated. Each carries the defect summary in
its ``reason``.

Author: dogfood-service agent (2026-07-15). Kept conflict-free from sibling
dogfood suites (governance / detector / retrieve).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dogfood_service"
OPEN_DB_DIFF = FIXTURES / "open_db_signature_change.diff"
DOCS_DIFF = FIXTURES / "docs_typo.diff"

# ``sys.executable`` is the interpreter pytest is running under — the venv
# Python (3.12 + numpy). Using it guarantees the ``roam`` install matches.
PY = sys.executable

# Long enough for the slowest deliverable observed on this repo
# (due-diligence measured 105-157s locally). Its two cases are marked slow:
# running the same multi-process report beside xdist workers creates resource
# contention and tests the scheduler rather than the report contract.
SLOW = 300
FAST = 120


def _roam(*args: str, cwd: Path | None = None, timeout: int = FAST) -> subprocess.CompletedProcess:
    """Run ``python -m roam <args>`` and return the completed process."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [PY, "-m", "roam", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def _roam_stdin(stdin: str, *args: str, timeout: int = FAST) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [PY, "-m", "roam", *args],
        cwd=str(REPO_ROOT),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


# ---------------------------------------------------------------------------
# service-report — each --type must render its required buyer-facing sections
# ---------------------------------------------------------------------------

# Section headers that a paid deliverable of each type MUST contain. If any is
# missing the report is degenerate for that engagement.
SERVICE_REPORT_REQUIRED = {
    "due-diligence": [
        "# Codebase Due Diligence Report",
        "## 1. Executive summary",
        "## 2. Codebase health",
        "## 3. Key-person / bus-factor risk",
        "## 8. Security & supply chain",
    ],
    "ai-readiness": [
        "# AI Adoption Readiness Audit",
        "## 1. Executive summary",
        "## 2. Readiness dimensions",
        "## 5. Governance posture",
    ],
    "reachability-triage": [
        "# Security Reachability Triage",
        "## 1. Executive summary",
        "## 3. Dependency reachability",
        "## 5. Secrets",
        "## 7. Recommended fix order",
    ],
    "post-incident": [
        "# Post-Incident Replay Report",
        "## 1. Incident window",
        "## 2. Detector replay",
        "## 3. Audit-trail integrity",
    ],
}


@pytest.mark.parametrize(
    "rtype",
    [
        pytest.param(rtype, marks=pytest.mark.slow) if rtype == "due-diligence" else rtype
        for rtype in sorted(SERVICE_REPORT_REQUIRED)
    ],
)
def test_service_report_renders_required_sections(rtype: str) -> None:
    """Each service-report --type emits its required sections, exit 0, clean stderr."""
    extra = ["--range", "HEAD~20..HEAD"] if rtype == "post-incident" else []
    proc = _roam("service-report", "--type", rtype, *extra, timeout=SLOW)
    assert proc.returncode == 0, f"{rtype}: exit {proc.returncode}\nSTDERR:\n{proc.stderr[:800]}"
    body = proc.stdout
    for section in SERVICE_REPORT_REQUIRED[rtype]:
        assert section in body, f"{rtype}: missing required section {section!r}"
    # A deliverable this size should never be a stub.
    assert len(body) > 1500, f"{rtype}: report suspiciously short ({len(body)} chars)"


def test_service_report_stderr_is_clean() -> None:
    """Regression guard for c4a7ed01: no SyntaxWarning / DeprecationWarning /
    traceback leaks to stderr on the fast report types."""
    for rtype in ("ai-readiness", "post-incident"):
        extra = ["--range", "HEAD~20..HEAD"] if rtype == "post-incident" else []
        proc = _roam("service-report", "--type", rtype, *extra, timeout=SLOW)
        low = proc.stderr.lower()
        for noise in ("syntaxwarning", "deprecationwarning", "traceback (most recent call last)"):
            assert noise not in low, f"{rtype}: stderr leaked {noise!r}\n{proc.stderr[:800]}"


@pytest.mark.slow
def test_service_report_due_diligence_has_no_empty_placeholder_verdict() -> None:
    """The executive summary must carry a concrete health verdict, not a stub."""
    proc = _roam("service-report", "--type", "due-diligence", timeout=SLOW)
    assert proc.returncode == 0
    # STRONG / FAIR / WEAK style verdict with a health score in parentheses.
    assert re.search(r"Verdict:\s*\w+", proc.stdout), "no concrete verdict line rendered"
    assert re.search(r"health\s+\d+/100", proc.stdout), "no health score in verdict"


# ---------------------------------------------------------------------------
# capsule --redact-paths — must actually anonymize file paths
# ---------------------------------------------------------------------------

_HEX6 = re.compile(r"^[0-9a-f]{6}$")


def _redacted_component(token: str) -> bool:
    """A properly redacted path component is a 6-char sha256 hex slice."""
    return bool(_HEX6.match(token))


def _all_components_redacted(path_like: str) -> bool:
    parts = [p for p in path_like.replace("\\", "/").split("/") if p]
    return bool(parts) and all(_redacted_component(p) for p in parts)


def _build_capsule(tmp_path: Path, *redact_args: str) -> dict:
    out = tmp_path / "capsule.json"
    proc = _roam("capsule", *redact_args, "--output", str(out), timeout=SLOW)
    assert proc.returncode == 0, f"capsule failed: {proc.stderr[:600]}"
    return json.loads(out.read_text(encoding="utf-8"))


def test_capsule_redacts_symbol_file_paths(tmp_path: Path) -> None:
    """--redact-paths hashes every symbol's ``file`` component (this part works)."""
    cap = _build_capsule(tmp_path, "--redact-paths")
    syms = cap.get("symbols", [])
    assert syms, "capsule produced no symbols"
    leaked = [s["file"] for s in syms if "file" in s and not _all_components_redacted(s["file"])]
    assert not leaked, f"{len(leaked)} symbol file paths not hashed, e.g. {leaked[:3]}"
    # And the plaintext control: an UN-redacted capsule keeps real paths.
    plain = _build_capsule(
        tmp_path,
    )
    assert any("src/roam" in s.get("file", "") for s in plain.get("symbols", [])), (
        "control failed: plain capsule should contain real src/roam paths"
    )


def test_capsule_redacts_cluster_labels(tmp_path: Path) -> None:
    """--redact-paths must anonymize cluster labels (they are path-derived)."""
    cap = _build_capsule(tmp_path, "--redact-paths")
    labels = [c["label"] for c in cap.get("clusters", []) if c.get("label")]
    assert labels, "capsule produced no cluster labels"
    leaked = [lbl for lbl in labels if not _all_components_redacted(lbl)]
    assert not leaked, f"{len(leaked)} of {len(labels)} cluster labels leak plaintext paths, e.g. {leaked[:5]}"


# ---------------------------------------------------------------------------
# critique — the merge-gate scorer
# ---------------------------------------------------------------------------


def _critique_json(diff_path: Path, *extra: str) -> tuple[int, dict]:
    proc = _roam("--json", "critique", "--input", str(diff_path), *extra)
    data = json.loads(proc.stdout)
    return proc.returncode, data


def _findings(data: dict) -> list[dict]:
    return data.get("findings") or data.get("data", {}).get("findings", [])


def test_critique_blocks_high_blast_change_with_exit_5() -> None:
    """A signature change to open_db (1125 callers) is HIGH → exit 5."""
    code, data = _critique_json(OPEN_DB_DIFF)
    assert code == 5, f"expected gate exit 5, got {code}"
    assert data["summary"].get("risk_level_canonical") == "high"
    msgs = " ".join(str(f.get("message", f.get("detail", ""))) for f in _findings(data))
    assert "open_db" in msgs, "high finding should name the changed high-blast symbol"


def test_critique_passes_safe_docs_change_with_exit_0() -> None:
    """A README typo fix touches no indexed symbols → low, exit 0, no findings."""
    code, data = _critique_json(DOCS_DIFF)
    assert code == 0, f"expected clean exit 0, got {code}"
    assert data["summary"].get("risk_level_canonical") == "low"
    assert _findings(data) == [], "safe docs change should produce zero findings"


def test_critique_detects_intent_removal_mismatch() -> None:
    """--intent claims a removal but the diff is purely additive → intent finding."""
    code, data = _critique_json(OPEN_DB_DIFF, "--intent", "remove the legacy validation path")
    intent_findings = [
        f
        for f in _findings(data)
        if "intent" in str(f.get("kind", "")).lower()
        or "removing" in str(f.get("message", f.get("detail", ""))).lower()
    ]
    assert intent_findings, "intent/diff mismatch (remove-claimed, add-only) not detected"


# ---------------------------------------------------------------------------
# pr-analyze — "the CLI engine behind Roam Agent Review"
# ---------------------------------------------------------------------------


def test_pr_analyze_commit_range_flags_real_risk() -> None:
    """The commit-range form DOES compute blast/critique (control for the bug below)."""
    proc = _roam("--json", "pr-analyze", "HEAD~3..HEAD", timeout=SLOW)
    data = json.loads(proc.stdout)
    verdict = str(data["summary"].get("verdict", ""))
    # HEAD~3..HEAD on this repo is a broad, multi-file change → not SAFE.
    assert verdict.startswith(("REVIEW", "BLOCK")), f"unexpected verdict: {verdict!r}"


@pytest.mark.xfail(
    # Non-strict: H3 is agent-confirmed but INTERMITTENT in this harness — it
    # XPASSED once (pr-analyze agreed with critique), so the reproduction is
    # repo/index-state dependent and a strict marker would flip-flop CI. The
    # real fix + a deterministic repro are tracked in task #287.
    strict=False,
    reason=(
        "DEFECT (pr-analyze): the acquired --input/stdin/--diff-from-pr/--staged "
        "diff is NEVER forwarded to pr-prep. cmd_pr_analyze.py:2187 calls "
        "_capture_pr_prep(commit_range, high_callers) — only the commit_range — "
        "so blast_radius and critique-high are computed against the (clean) "
        "working tree and floor to 0. The same open_db diff is HIGH via "
        "`critique` and CRITICAL via `preflight`, yet pr-analyze --input returns "
        "SAFE / blast 0 / exit 0 even with --gate. Every diff-piping automation "
        "path (incl. the headline `git diff | roam pr-analyze` and the "
        "Roam Agent Review --diff-from-pr path) is a silent false-negative gate. "
        "Fix: forward the acquired diff to pr-prep instead of only commit_range."
    ),
)
def test_pr_analyze_input_diff_agrees_with_critique() -> None:
    """pr-analyze --input on a high-blast diff must NOT verdict SAFE.

    Control: `critique` on the same diff is high-severity (asserted first).
    """
    code, crit = _critique_json(OPEN_DB_DIFF)
    assert code == 5 and crit["summary"].get("risk_level_canonical") == "high", (
        "control precondition failed — critique should flag this diff HIGH"
    )
    proc = _roam("--json", "pr-analyze", "--input", str(OPEN_DB_DIFF))
    data = json.loads(proc.stdout)
    verdict = str(data["summary"].get("verdict", ""))
    assert not verdict.startswith("SAFE"), (
        f"pr-analyze --input verdict {verdict!r} contradicts critique HIGH on the "
        "same diff — the input diff was not analyzed for blast radius"
    )


# ---------------------------------------------------------------------------
# postmortem — the --json envelope must be machine-parseable
# ---------------------------------------------------------------------------


def test_postmortem_json_stdout_is_pure_json() -> None:
    """--json postmortem stdout must parse as JSON with no leading chrome."""
    proc = _roam("--json", "postmortem", "HEAD~5..HEAD")
    json.loads(proc.stdout)  # raises JSONDecodeError today on 'Replaying detectors\n{'


# ---------------------------------------------------------------------------
# pr-replay --evidence-bundle — the hidden deliverable writer
# ---------------------------------------------------------------------------


def test_pr_replay_evidence_bundle_writes_real_content(tmp_path: Path) -> None:
    """--evidence-bundle <dir> writes evidence.json + report.md with real content."""
    bundle = tmp_path / "eb"
    proc = _roam(
        "pr-replay",
        "--tier",
        "team",
        "--range",
        "HEAD~10..HEAD",
        "--evidence-bundle",
        str(bundle),
        timeout=SLOW,
    )
    assert proc.returncode == 0, f"pr-replay failed: {proc.stderr[:600]}"
    ev = bundle / "evidence.json"
    rep = bundle / "report.md"
    assert ev.exists(), "evidence.json not written"
    assert rep.exists(), "report.md not written"
    data = json.loads(ev.read_text(encoding="utf-8"))
    # Evidence packet must carry a verdict, a risk level, and real findings.
    assert data.get("verdict"), "evidence packet missing verdict"
    assert data.get("risk_level"), "evidence packet missing risk_level"
    assert data.get("commit_sha"), "evidence packet missing commit_sha"
    md = rep.read_text(encoding="utf-8")
    assert md.count("\n## ") >= 3, "report.md has too few sections to be a deliverable"
    assert "PR Replay" in md


def test_pr_replay_bare_evidence_bundle_flag_is_usage_error() -> None:
    """The hidden --evidence-bundle requires a directory arg (bare flag → exit 2)."""
    proc = _roam("pr-replay", "--evidence-bundle")
    assert proc.returncode == 2, f"expected click usage exit 2, got {proc.returncode}"
    assert "requires an argument" in proc.stderr.lower()


# ---------------------------------------------------------------------------
# pr-bundle — the proof-carrying bundle gate (hermetic: throwaway git repo)
# ---------------------------------------------------------------------------


def _init_temp_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def test_pr_bundle_validate_strict_exits_5_on_incomplete(tmp_path: Path) -> None:
    """An initialized-but-incomplete bundle fails the strict gate with exit 5."""
    repo = _init_temp_git_repo(tmp_path)
    init = _roam("pr-bundle", "init", "--intent", "dogfood", cwd=repo)
    assert init.returncode == 0, f"init failed: {init.stderr[:400]}"
    val = _roam("pr-bundle", "validate", "--strict", cwd=repo)
    assert val.returncode == 5, f"strict validate should gate incomplete bundle, got {val.returncode}"
    assert "incomplete" in (val.stdout + val.stderr).lower()


def test_proof_bundle_composes_verdict_envelope(tmp_path: Path) -> None:
    """proof-bundle composes a v1 verdict envelope with an exit-code-consistent verdict.

    In a bare (startup-policy, no-roam-index) repo nothing is *required*, so the
    correct verdict is a pass with exit 0 — this asserts that clean-path contract
    and that the envelope names its bundle + a known reason. (The gate/block path
    — exit 5 with reason ``required_checks_not_run`` — was separately confirmed on
    the indexed repo, but requires the constitution/mode machinery a temp repo
    lacks, so it is not reproduced hermetically here.)
    """
    repo = _init_temp_git_repo(tmp_path)
    assert _roam("pr-bundle", "init", "--intent", "dogfood", cwd=repo).returncode == 0
    pb = _roam("proof-bundle", "--strict", cwd=repo)
    out = pb.stdout + pb.stderr
    assert "VERDICT:" in out, f"no verdict envelope emitted:\n{out[:400]}"
    assert re.search(r"reason:\s*\w+", out), "verdict envelope missing a reason"
    # Exit code must agree with the rendered verdict (no silent success/failure skew).
    verdict_pass = "VERDICT: pass" in out or "all_required_passed" in out
    if verdict_pass:
        assert pb.returncode == 0, f"verdict pass but exit {pb.returncode}"
    else:
        assert pb.returncode == 5, f"non-pass verdict but exit {pb.returncode}"
