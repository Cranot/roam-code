"""W761-vulns-secrets — severity-vocabulary parity for cmd_vulns + cmd_secrets.

Pattern 3a (cross-command metric divergence). Both commands report
``severity_breakdown`` / ``by_severity`` envelope buckets and ``severity``
values inside their findings. Pre-W547 each owned its own bucket dict and
inline rank table; pre-W566 each rolled its own breakdown loop. The two
commands are now wired onto the canonical helpers in
:mod:`roam.output._severity`:

* :data:`SEVERITY_LEVELS` — 4-tier canonical roam->SARIF vocabulary.
* :data:`SEVERITY_ALIASES` — CVSS spellings (``high``/``medium``/``low``)
  + ``note`` + ``unknown`` resolving back into the canonical set.
* :func:`severity_rank` — single source of truth for severity ORDER over
  the 9-key 5-tier-plus-aliases vocab.
* :func:`severity_breakdown` — single bucketing helper, vocab-parametric.
* :func:`to_sarif_level` — single SARIF level projection.

What this regression test pins
------------------------------

1. **cmd_vulns + cmd_secrets import the canonical helpers**, not local
   rank tables or local breakdown loops. AST scan asserts the import.
2. **No inline ``severity_rank``-shaped dict literal**. Both modules
   parse as ASTs with no rank-table assignment in the W564-shape
   (``{<severity-key>: <int>, ...}`` with target name matching
   ``/sever/i``). The narrow ``_LEVEL_ORDER`` list in cmd_vulns is a
   LIST (not a dict) and encodes the R22 confidence-derivation rule on a
   3-element ``{low, medium, high}`` axis — outside W547 severity scope.
3. **The cmd_vulns ``_severity_breakdown`` thin wrapper round-trips
   the canonical helper byte-identically** on a CVSS-5-tier fixture
   plus an unknown-bucket label, plus None severity.
4. **The cmd_secrets ``by_severity`` envelope key carries the
   intentional narrow 3-tier vocab** (``high`` / ``medium`` / ``low``).
   The narrowing is deliberate — every secret pattern emits one of those
   three at module-load time, so widening to the CVSS-5 vocab would
   create permanent-zero buckets without adding signal. Drift-guard:
   patterns must keep emitting only the narrow vocab; introducing a
   ``critical`` pattern without widening the breakdown call would silently
   drop those findings from the count.
5. **cmd_secrets ``--severity`` click.Choice carries the W547 canonical
   7-tier + ``all`` sentinel** (per W1005-followup-C). Drift-guard: a
   future edit that narrows the Choice back to the pre-W1005 3-tier
   ``{high, medium, low}`` would break the parity contract with
   :func:`severity_rank`.

Scope discipline (per task brief)
---------------------------------

* ONLY exercises cmd_vulns + cmd_secrets. Does NOT touch cmd_health /
  cmd_complexity / cmd_smells (already wired by W1005 / W761 sibling
  waves).
* Does NOT modify ``roam.output._severity`` itself — that contract is
  pinned by ``tests/test_w547_severity_drift.py``.
* Does NOT change the SARIF level mapping (canonical -> error / warning
  / note). Both commands already route through ``to_sarif_level`` /
  ``_to_level``; that boundary stays untouched.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Canonical helpers under test
from roam.output._severity import (
    SEVERITY_ALIASES,
    SEVERITY_LEVELS,
    severity_breakdown,
    severity_rank,
)
from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"
CMD_VULNS = SRC_ROOT / "commands" / "cmd_vulns.py"
CMD_SECRETS = SRC_ROOT / "commands" / "cmd_secrets.py"

# The 9-key severity vocab severity_rank() accepts as defined keys
# (plus aliases). Used to assert pattern emit values land in-vocab.
_CANONICAL_SEVERITY_VOCAB: frozenset[str] = SEVERITY_LEVELS | frozenset(SEVERITY_ALIASES.keys())


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# 1. Both commands import the canonical helpers
# ---------------------------------------------------------------------------


def test_cmd_vulns_imports_canonical_severity_helpers() -> None:
    """cmd_vulns sources severity vocabulary from roam.output._severity."""
    text = CMD_VULNS.read_text(encoding="utf-8")
    assert "roam.output._severity" in text, "W761-vulns-secrets: cmd_vulns must import from roam.output._severity"
    # severity_breakdown helper is the W566 canonical bucketing entry
    # point — present as ``_canonical_severity_breakdown`` alias.
    assert "severity_breakdown" in text, "W761-vulns-secrets: cmd_vulns must reference severity_breakdown"
    # severity_rank is the W564 canonical ordering entry point
    assert "severity_rank" in text, "W761-vulns-secrets: cmd_vulns must reference severity_rank"
    # SARIF projection is canonical too
    assert "to_sarif_level" in text, "W761-vulns-secrets: cmd_vulns must reference to_sarif_level"


def test_cmd_secrets_imports_canonical_severity_helpers() -> None:
    """cmd_secrets sources severity vocabulary from roam.output._severity."""
    text = CMD_SECRETS.read_text(encoding="utf-8")
    assert "roam.output._severity" in text, "W761-vulns-secrets: cmd_secrets must import from roam.output._severity"
    assert "severity_breakdown" in text, "W761-vulns-secrets: cmd_secrets must reference severity_breakdown"
    assert "severity_rank" in text, "W761-vulns-secrets: cmd_secrets must reference severity_rank"


# ---------------------------------------------------------------------------
# 2. No inline severity-rank dict literal
# ---------------------------------------------------------------------------


def _has_inline_severity_rank_dict(tree: ast.Module) -> list[int]:
    """Return line numbers of any inline ``{severity: int}`` rank dict.

    Mirrors the W564 drift-guard predicate: at least 2 string-keyed
    integer pairs where every string key is in the canonical severity
    vocab. The R22 ``_LEVEL_ORDER = ["low", "medium", "high"]`` in
    cmd_vulns is a LIST (not a dict) — outside this check by design.
    """
    rank_vocab = _CANONICAL_SEVERITY_VOCAB | frozenset(s.upper() for s in _CANONICAL_SEVERITY_VOCAB)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs: list[tuple[str, int]] = []
        has_non_sev_key = False
        for k, v in zip(node.keys, node.values):
            if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                continue
            if k.value not in rank_vocab:
                has_non_sev_key = True
                continue
            if not isinstance(v, ast.Constant) or isinstance(v.value, bool):
                continue
            if not isinstance(v.value, int):
                continue
            pairs.append((k.value, v.value))
        if has_non_sev_key or len(pairs) < 2:
            continue
        if len({val for _, val in pairs}) >= 2:
            hits.append(node.lineno)
    return hits


def test_cmd_vulns_no_inline_severity_rank_dict() -> None:
    """cmd_vulns must not carry a private severity->int rank dict."""
    hits = _has_inline_severity_rank_dict(_parse(CMD_VULNS))
    assert not hits, (
        f"W761-vulns-secrets: cmd_vulns has inline severity-rank dict "
        f"literal at lines {hits} — use severity_rank() from "
        f"roam.output._severity"
    )


def test_cmd_secrets_no_inline_severity_rank_dict() -> None:
    """cmd_secrets must not carry a private severity->int rank dict."""
    hits = _has_inline_severity_rank_dict(_parse(CMD_SECRETS))
    assert not hits, (
        f"W761-vulns-secrets: cmd_secrets has inline severity-rank dict "
        f"literal at lines {hits} — use severity_rank() from "
        f"roam.output._severity"
    )


# ---------------------------------------------------------------------------
# 3. cmd_vulns._severity_breakdown round-trips the canonical helper
# ---------------------------------------------------------------------------


def test_cmd_vulns_severity_breakdown_matches_canonical() -> None:
    """The cmd_vulns thin wrapper is byte-identical to severity_breakdown."""
    from roam.commands.cmd_vulns import _severity_breakdown

    # Fixture covers every CVSS-5 tier + unknown bucket routes (None,
    # bogus string, mixed case).
    vulns = [
        {"severity": "critical"},
        {"severity": "CRITICAL"},  # case-insensitive
        {"severity": "high"},
        {"severity": "high"},
        {"severity": "medium"},
        {"severity": "low"},
        {"severity": "bogus"},  # -> unknown
        {"severity": None},  # -> unknown
    ]
    expected = severity_breakdown(vulns)
    assert _severity_breakdown(vulns) == expected
    # Spot-check the contract: 5-tier + unknown vocab is the default
    assert expected == {
        "critical": 2,
        "high": 2,
        "medium": 1,
        "low": 1,
        "unknown": 2,
    }


# ---------------------------------------------------------------------------
# 4. cmd_secrets envelope by_severity is intentionally narrow 3-tier
# ---------------------------------------------------------------------------


def test_cmd_secrets_patterns_emit_only_narrow_vocab() -> None:
    """Every secret pattern emits one of {high, medium, low}.

    Drift-guard: if a future pattern declares ``severity="critical"``
    (or any token outside the 3-tier subset), the envelope
    ``by_severity`` call must widen its ``vocab=`` argument to match —
    otherwise the new finding silently drops out of the count.

    The narrow 3-tier vocab is also a strict subset of the canonical
    9-key severity_rank() vocab — so no pattern can emit a label that
    severity_rank() collapses to -1.
    """
    from roam.commands.cmd_secrets import _SECRET_PATTERN_DEFS

    allowed = frozenset({"high", "medium", "low"})
    bad: list[tuple[str, str]] = []
    for pat in _SECRET_PATTERN_DEFS:
        sev = pat.get("severity", "")
        if sev not in allowed:
            bad.append((pat.get("name", "?"), sev))
        # Every emit token must also have a finite canonical rank
        assert severity_rank(sev) >= 0, (
            f"W761-vulns-secrets: secret pattern {pat.get('name')!r} emits "
            f"severity {sev!r} which severity_rank() collapses to -1"
        )

    assert not bad, (
        f"W761-vulns-secrets: secret patterns emit out-of-vocab severity "
        f"{bad}; either rename to high/medium/low OR widen the envelope "
        f"``by_severity`` call's vocab= argument in cmd_secrets.py"
    )


def test_cmd_secrets_envelope_by_severity_is_narrow_3_tier() -> None:
    """Envelope ``by_severity`` carries zero-padded {high, medium, low}.

    Reproduces the call shape used inside ``cmd_secrets.secrets``:
    ``severity_breakdown(findings, key="severity", vocab=("high","medium","low"),
    unknown_bucket=None, drop_zero=False)``. Empty findings -> all-zero
    3-tier dict (the contract the verdict-build expects).
    """
    result = severity_breakdown(
        [],
        key="severity",
        vocab=("high", "medium", "low"),
        unknown_bucket=None,
        drop_zero=False,
    )
    assert result == {"high": 0, "medium": 0, "low": 0}

    # Non-empty input — unknown labels drop silently (cmd_secrets contract)
    result2 = severity_breakdown(
        [
            {"severity": "high"},
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
            {"severity": "critical"},  # outside narrow vocab -> dropped
        ],
        key="severity",
        vocab=("high", "medium", "low"),
        unknown_bucket=None,
        drop_zero=False,
    )
    assert result2 == {"high": 2, "medium": 1, "low": 1}


# ---------------------------------------------------------------------------
# 5. cmd_secrets --severity click.Choice covers the W547 canonical 7-tier
# ---------------------------------------------------------------------------


def test_cmd_secrets_severity_choice_is_canonical_7_tier() -> None:
    """``--severity`` click.Choice accepts every canonical token + ``all``.

    Per W1005-followup-C the cmd_secrets CLI was widened from 3-tier
    ``{high, medium, low}`` to the canonical 7-tier so agents that read
    severity_rank()'s docstring can pass any defined token (a regression
    against the pre-W1005 ``roam secrets --severity warning`` parse
    error). The ``all`` sentinel is preserved as a no-floor token —
    handled distinctly in ``scan_file`` / ``scan_project``.
    """
    from roam.commands.cmd_secrets import secrets

    # Locate the --severity click.Option and read its choice values
    sev_opt = None
    for p in secrets.params:
        if p.name == "severity":
            sev_opt = p
            break
    assert sev_opt is not None, "cmd_secrets must expose a --severity option"

    # click.Choice exposes its accepted values as .type.choices
    choices = set(sev_opt.type.choices)
    expected = {
        "all",
        "critical",
        "error",
        "high",
        "warning",
        "medium",
        "low",
        "info",
    }
    assert choices == expected, (
        f"W761-vulns-secrets: cmd_secrets --severity choices drifted; "
        f"expected {sorted(expected)}, got {sorted(choices)}"
    )

    # Every non-``all`` choice must have a finite canonical rank — pins
    # the parity contract with severity_rank() over the CLI parse boundary.
    for choice in choices - {"all"}:
        assert severity_rank(choice) >= 0, (
            f"W761-vulns-secrets: cmd_secrets accepts --severity {choice!r} "
            f"but severity_rank({choice!r}) is -1 — the floor filter would "
            f"never trigger"
        )
