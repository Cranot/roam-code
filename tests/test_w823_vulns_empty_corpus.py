"""W823 — Empty-corpus smoke for `roam vulns`.

Security-sensitive: a vulnerability scanner that emits a default "SAFE" /
"no vulnerabilities" verdict on an empty corpus would silently tell a
caller the codebase is clean when in fact no scanner has touched it.
This is Pattern 2 (silent fallback) at HIGH severity.

`src/roam/commands/cmd_vulns.py` has an explicit Fix E (lines 405-426)
that distinguishes the two states:

  * `state == "no_scan"` + `partial_success: True`
    Verdict mentions the vulnerabilities table is empty and points the
    caller at `roam vulns --import-file`.
  * `state == "scanned"` + `partial_success: False`
    Verdict reports findings (or genuine "No vulnerabilities found"
    after a scan).

This test pins that Fix E remains in force on the empty-corpus path —
the most common false-positive surface for a "SAFE" verdict.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

from click.testing import CliRunner

# Reuse the conftest helpers used by the rest of the suite.
sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# Verdict fragments that would indicate Pattern 2 silent-fallback if
# emitted on an empty corpus. Each fragment is matched case-insensitively
# against the verdict string. Adding to this list is appropriate; removing
# requires a Pattern 2 audit.
_FORBIDDEN_VERDICT_FRAGMENTS = (
    "no vulnerabilities found",
    "no vulnerabilities in the database",
    "safe",
    "secure",
    "clean",
    "all clear",
)


def test_vulns_empty_corpus_distinguishes_no_scan_from_no_findings(tmp_path, monkeypatch):
    """On an empty corpus with no scanner ever ingested, `roam vulns
    --json` must:

      1. exit 0,
      2. emit a structured envelope,
      3. set ``summary.state == "no_scan"`` (NOT "scanned"),
      4. set ``summary.partial_success is True``,
      5. emit a verdict that mentions the empty / unscanned state,
      6. NEVER emit a verdict that reads as "this codebase is safe",
      7. carry non-empty ``agent_contract.facts``.
    """
    proj = tmp_path / "empty_corpus_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    # Single empty .py file — minimum viable corpus.
    (proj / "empty.py").write_text("", encoding="utf-8")
    git_init(proj)
    index_in_process(proj)
    monkeypatch.chdir(proj)

    runner = CliRunner()
    from roam.cli import cli

    result = runner.invoke(cli, ["--json", "vulns"], catch_exceptions=False)

    # 1. Exit 0.
    assert result.exit_code == 0, (
        f"`roam --json vulns` on empty corpus exited {result.exit_code}, expected 0.\n--- stdout ---\n{result.output}"
    )

    # 2. Structured envelope.
    env = _json.loads(result.output)
    assert env.get("command") == "vulns", f"Expected envelope.command == 'vulns', got {env.get('command')!r}"
    summary = env.get("summary") or {}
    assert summary, "Envelope must carry a non-empty `summary` block."

    # 3. state == "no_scan" — Fix E discipline.
    state = summary.get("state")
    assert state == "no_scan", (
        f"Empty corpus with no scanner ingested must surface "
        f"`summary.state == 'no_scan'` (Pattern 2 / Fix E in "
        f"cmd_vulns.py:419). Got state={state!r}, summary={summary!r}"
    )

    # 4. partial_success is True.
    assert summary.get("partial_success") is True, (
        "Empty corpus / no-scan state MUST set `partial_success: True` "
        "so consumers know the verdict is not a fully-resolved SAFE. "
        f"Got summary={summary!r}"
    )

    # 5. Verdict mentions the empty / unscanned state.
    verdict = summary.get("verdict", "")
    assert isinstance(verdict, str) and verdict.strip(), (
        f"Empty corpus verdict must be a non-empty string. Got {verdict!r}"
    )
    verdict_lc = verdict.lower()
    assert any(token in verdict_lc for token in ("no vulnerability scan", "no scan", "empty", "import-file")), (
        "Empty-corpus verdict must explicitly mention the no-scan / "
        "empty-table state and ideally point at `--import-file`. "
        f"Got verdict={verdict!r}"
    )

    # 6. CRITICAL: verdict must NOT read as a clean / safe / secure
    # signal. This is the silent-fallback bug the test guards against.
    for forbidden in _FORBIDDEN_VERDICT_FRAGMENTS:
        assert forbidden not in verdict_lc, (
            f"SECURITY-SENSITIVE: empty-corpus verdict contained "
            f"{forbidden!r}, which silently reads as 'codebase is safe' "
            f"when in fact no scanner has been ingested. This is the "
            f"Pattern 2 silent-fallback bug. verdict={verdict!r}"
        )

    # 7. agent_contract.facts is non-empty (auto-injected by
    # formatter.json_envelope -> _derive_agent_contract).
    contract = env.get("agent_contract") or {}
    facts = contract.get("facts") or []
    assert isinstance(facts, list) and facts, (
        f"agent_contract.facts must be a non-empty list on the empty-corpus path. Got contract={contract!r}"
    )
    # Each fact should be a non-empty string.
    for i, f in enumerate(facts):
        assert isinstance(f, str) and f.strip(), (
            f"agent_contract.facts[{i}] must be a non-empty string. Got facts={facts!r}"
        )


def test_vulns_empty_corpus_total_is_zero(tmp_path, monkeypatch):
    """Sanity tail: total == 0, reachable_count == 0, by_severity is
    empty (or all-zero) on the no-scan path.

    Catches the inverse failure where someone silently populates the
    vulnerabilities table at `roam init` time.
    """
    proj = tmp_path / "empty_corpus_proj_counts"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    (proj / "empty.py").write_text("", encoding="utf-8")
    git_init(proj)
    index_in_process(proj)
    monkeypatch.chdir(proj)

    runner = CliRunner()
    from roam.cli import cli

    result = runner.invoke(cli, ["--json", "vulns"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    env = _json.loads(result.output)
    summary = env["summary"]

    assert summary.get("total") == 0
    assert summary.get("reachable_count") == 0
    # by_severity should be an empty dict (filtered to non-zero counts in
    # _severity_breakdown) or contain only zero counts.
    by_sev = summary.get("by_severity") or {}
    assert all(v == 0 for v in by_sev.values()), f"Empty corpus must not report any severity counts. Got {by_sev!r}"

    # Top-level `vulnerabilities` payload must be an empty list (NOT
    # absent, NOT None — consumers rely on the key being a list).
    vulns = env.get("vulnerabilities")
    assert isinstance(vulns, list) and len(vulns) == 0, f"Empty corpus must emit `vulnerabilities: []`. Got {vulns!r}"
