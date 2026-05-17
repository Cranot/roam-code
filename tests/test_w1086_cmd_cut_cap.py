"""W1086 — Pattern-1A disclosure on cmd_cut hard-cap refusal.

The pre-W1086 envelope on the `sym_count > _MAX_GRAPH_SYMBOLS` branch had:

    summary = {"verdict": msg, "symbol_count": sym_count}

— structurally indistinguishable from "analyzed cleanly with no findings" to
any consumer that only reads `summary.partial_success` / `summary.state`.

W1086 (Pattern-1A canonical fix; mirrors W1085 on cmd_fingerprint) adds:

- `summary.partial_success: True`
- `summary.state: "graph_too_large"`
- `summary.cap_threshold` + `summary.actual_count` for agent disclosure
- top-level `hint` with an imperative next-step

The test forces the over-cap path by monkeypatching `_MAX_GRAPH_SYMBOLS` down
to 0 (so any non-empty index trips the branch).

LAW 4 anchor: verdict terminal token is `symbols` (in
`formatter.concrete_plural_terminals`).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli


def _make_minimal_corpus(tmp: Path) -> None:
    """Build a tiny git-init'd Python corpus and index it.

    The corpus has 1+ symbols so the over-cap branch is the one we exercise.
    """
    (tmp / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "init",
            "-q",
        ],
        cwd=tmp,
        check=True,
    )
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        result = runner.invoke(cli, ["init"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        os.chdir(cwd)


class TestCutHardCapDisclosure:
    """W1086 — over-cap refusal must surface partial_success + state."""

    def test_over_cap_envelope_discloses_graph_too_large(self, tmp_path, monkeypatch):
        _make_minimal_corpus(tmp_path)

        # Force the over-cap path by lowering the threshold. The fixture has
        # 2 symbols; setting the cap to 0 means any non-empty index trips
        # the branch.
        from roam.commands import cmd_cut as cut_mod

        monkeypatch.setattr(cut_mod, "_MAX_GRAPH_SYMBOLS", 0)

        runner = CliRunner()
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = runner.invoke(cli, ["--json", "cut"], catch_exceptions=False)
        finally:
            os.chdir(cwd)

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output) if result.output.strip() else {}
        assert payload.get("command") == "cut"
        summary = payload.get("summary") or {}

        # --- Pattern-1A canonical disclosure ---

        # state is a closed-enum string we can pin exactly.
        assert summary.get("state") == "graph_too_large", (
            f"summary.state must be 'graph_too_large' on over-cap refusal, got {summary.get('state')!r}"
        )

        # partial_success must be True — that's what distinguishes "refused
        # to analyze" from "analyzed cleanly with no findings".
        assert summary.get("partial_success") is True, (
            f"summary.partial_success must be True on over-cap refusal; got {summary.get('partial_success')!r}"
        )

        # Verdict surfaces the refusal in human-readable form.
        verdict = (summary.get("verdict") or "").lower()
        assert "skipped" in verdict or "too large" in verdict or "cap" in verdict, (
            f"verdict {verdict!r} did not disclose over-cap refusal"
        )

        # Disclosure fields the agent contract surfaces.
        assert summary.get("cap_threshold") == 0
        actual = summary.get("actual_count")
        assert isinstance(actual, int) and actual >= 1, (
            f"summary.actual_count should be the real sym_count, got {actual!r}"
        )

        # Hint must be present and actionable (imperative).
        hint = payload.get("hint") or ""
        assert hint, "envelope must carry a non-empty hint on over-cap refusal"
        assert any(
            hint.lower().startswith(verb) for verb in ("index", "narrow", "raise", "increase", "use", "run", "set")
        ), f"hint {hint!r} should start with an imperative verb"

    def test_under_cap_normal_run_unaffected(self, tmp_path, monkeypatch):
        """Sanity: the disclosure path is only entered above the cap.

        With the default cap (5000), a 2-symbol fixture must take the
        normal cut-analysis path and NOT enter the W1086 graph_too_large
        branch.
        """
        _make_minimal_corpus(tmp_path)

        runner = CliRunner()
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = runner.invoke(cli, ["--json", "cut"], catch_exceptions=False)
        finally:
            os.chdir(cwd)

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output) if result.output.strip() else {}
        summary = payload.get("summary") or {}

        # Normal path: state must NOT be the W1086 disclosure.
        assert summary.get("state") != "graph_too_large", (
            f"under-cap run should not enter W1086 disclosure; summary={summary!r}"
        )
