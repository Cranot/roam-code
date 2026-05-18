"""W547 drift-guard — roam->SARIF severity contract must source from
:mod:`roam.output._severity`, not inline tables.

Pre-W547 the canonical mapping (CRITICAL/ERROR -> "error", HIGH/WARNING
-> "warning", MEDIUM/LOW/INFO -> "note") lived only inside
``roam.output.sarif._LEVEL_MAP``. Every other SARIF-emitting command
either imported ``_to_level`` from that module or rolled its own table
inline. Pattern-3a metric-divergence: different commands could end up
disagreeing on whether ``"high"`` maps to SARIF warning or note, with
real CI consequences (W531 lesson: silently downgrading CRITICAL to
``note`` breaks GitHub Code Scanning gates).

W547 (this drift-guard) consolidates the contract into
:mod:`roam.output._severity` and lints the source tree to prove no new
site re-introduces an inline table.

What this test asserts
----------------------

For every ``src/roam/**/*.py`` file outside the allowlist:

1. **No inline dict that mirrors the SARIF severity contract.** If a
   dict literal maps ``"critical"`` / ``"error"`` / ``"high"`` /
   ``"warning"`` / ``"medium"`` / ``"low"`` / ``"info"`` keys to
   ``"error"`` / ``"warning"`` / ``"note"`` values, the site must
   import :func:`to_sarif_level` from :mod:`roam.output._severity`
   instead. Heuristic: at least two distinct severity-key/SARIF-level
   pairings in the SAME dict literal triggers the lint.

2. **No private import of the legacy shim.** Outside the canonical
   module + its back-compat shim in ``output/sarif.py``, no file
   should ``from roam.output.sarif import _to_level`` — use the
   public :func:`roam.output._severity.to_sarif_level` instead.

The allowlist captures the canonical module itself plus the back-compat
shim. The W531 SARIF-conversion paths inside ``output/sarif.py`` are
already migrated; the shim is the boundary.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"

# Sites permitted to define / re-export the legacy SARIF severity table.
# The canonical module IS the source of truth; the sarif.py shim is the
# documented back-compat boundary.
_ALLOWLIST: dict[str, str] = {
    "output/_severity.py": "canonical module — defines the contract",
    "output/sarif.py": "back-compat shim — re-exports _LEVEL_MAP / _to_level",
}

# W564 — sites allowed to keep a local severity-rank table because the
# rank lives in a domain-specific vocabulary that the canonical
# 4-tier (``critical/error/warning/info``) + alias set does NOT cover.
# Each entry MUST cite the divergent vocab inline.
_RANK_ALLOWLIST: dict[str, str] = {
    # ``OK`` token + ``WARNING`` ≡ ``MEDIUM`` equivalence is a preflight
    # domain semantic (W564 follow-up: split into local clean-pass map).
    "commands/cmd_preflight.py": "OK clean-pass marker; WARNING≡MEDIUM merge",
    # ``breaking`` is an API-change severity, not in canonical vocab.
    "commands/cmd_api_changes.py": "breaking/warning/info — API-change domain",
    # W598: cmd_tx_boundaries.py used to need a W564 allowlist entry
    # because its rank table was misleadingly named ``_SEVERITY_RANK``.
    # After the W598 rename to ``_TX_CLASSIFICATION_RANK``, the target
    # name no longer matches the W564 ``sever|level_order`` pattern and
    # the file is naturally out of scope — no allowlist entry needed.
}

# Severity-vocabulary tokens. A dict whose KEYS draw from this set AND
# whose VALUES draw from {error, warning, note} with at least two distinct
# (key, value) pairs is treated as a SARIF severity table.
_SEVERITY_KEYS = frozenset(
    {
        "critical",
        "error",
        "high",
        "warning",
        "medium",
        "low",
        "info",
        "note",
        "CRITICAL",
        "ERROR",
        "HIGH",
        "WARNING",
        "MEDIUM",
        "LOW",
        "INFO",
        "NOTE",
    }
)
_SARIF_LEVELS = frozenset({"error", "warning", "note"})


def _iter_source_files() -> list[Path]:
    return [p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def _is_severity_table(dict_node: ast.Dict) -> bool:
    """Return True when *dict_node* looks like a SARIF severity table.

    Counts (key, value) pairs where:
      * key is a string constant drawn from :data:`_SEVERITY_KEYS`, and
      * value is a string constant drawn from :data:`_SARIF_LEVELS`.

    Two or more such pairs in the same dict literal is the trigger —
    one isolated pair could be a legitimate domain mapping that
    coincidentally uses these labels.
    """
    matches = 0
    for k, v in zip(dict_node.keys, dict_node.values):
        if not isinstance(k, ast.Constant) or not isinstance(v, ast.Constant):
            continue
        if not isinstance(k.value, str) or not isinstance(v.value, str):
            continue
        if k.value in _SEVERITY_KEYS and v.value in _SARIF_LEVELS:
            matches += 1
            if matches >= 2:
                return True
    return False


def _find_inline_severity_tables(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(SRC_ROOT).as_posix()
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and _is_severity_table(node):
            hits.append(f"{rel}:{node.lineno}")
    return hits


def _find_legacy_imports(path: Path) -> list[str]:
    """Return import sites importing ``_to_level`` / ``_LEVEL_MAP`` from
    :mod:`roam.output.sarif` (the legacy shim).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(SRC_ROOT).as_posix()
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "roam.output.sarif":
            for alias in node.names:
                if alias.name in {"_to_level", "_LEVEL_MAP"}:
                    hits.append(f"{rel}:{node.lineno}: {alias.name}")
    return hits


def test_no_inline_sarif_severity_tables() -> None:
    """Every roam->SARIF severity table must live in roam.output._severity."""
    violations: list[str] = []
    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        violations.extend(_find_inline_severity_tables(path))
    assert not violations, (
        "W547: inline roam->SARIF severity table — import to_sarif_level "
        "from roam.output._severity instead:\n  " + "\n  ".join(violations)
    )


def test_no_legacy_to_level_imports() -> None:
    """No site outside the shim should import _to_level / _LEVEL_MAP."""
    violations: list[str] = []
    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        violations.extend(_find_legacy_imports(path))
    assert not violations, (
        "W547: importing private _to_level / _LEVEL_MAP from "
        "roam.output.sarif — use the public to_sarif_level from "
        "roam.output._severity instead:\n  " + "\n  ".join(violations)
    )


def test_canonical_module_surface_stable() -> None:
    """The canonical module exports the expected closed-enum vocabulary."""
    from roam.output import _severity

    assert _severity.SEVERITY_LEVELS == frozenset({"critical", "error", "warning", "info"})
    # Aliases resolve into the canonical set
    assert _severity.SEVERITY_ALIASES["high"] == "warning"
    assert _severity.SEVERITY_ALIASES["medium"] == "info"
    assert _severity.SEVERITY_ALIASES["low"] == "info"

    # SARIF contract (the W531 baseline)
    assert _severity.to_sarif_level("CRITICAL") == "error"
    assert _severity.to_sarif_level("error") == "error"
    assert _severity.to_sarif_level("high") == "warning"
    assert _severity.to_sarif_level("warning") == "warning"
    assert _severity.to_sarif_level("medium") == "note"
    assert _severity.to_sarif_level("low") == "note"
    assert _severity.to_sarif_level("info") == "note"
    # Unknown -> note (never accidentally fail a CI gate on a typo)
    assert _severity.to_sarif_level("bogus") == "note"
    assert _severity.to_sarif_level(None) == "note"


def test_legacy_shim_round_trips() -> None:
    """The sarif.py back-compat shim resolves every pre-W547 key."""
    from roam.output.sarif import _LEVEL_MAP, _to_level

    # Pre-W547 baseline — every key in the W531 shipped table must
    # still resolve to the same SARIF level.
    expected = {
        "CRITICAL": "error",
        "ERROR": "error",
        "HIGH": "warning",
        "WARNING": "warning",
        "MEDIUM": "note",
        "LOW": "note",
        "INFO": "note",
    }
    for k, v in expected.items():
        assert _LEVEL_MAP[k] == v, f"{k} should map to {v}"
        assert _to_level(k) == v, f"_to_level({k!r}) should be {v}"

    # Case-insensitive (W531 contract)
    assert _to_level("critical") == "error"
    assert _to_level("Warning") == "warning"
    # Unknown -> note (CI-gate safety)
    assert _to_level("bogus") == "note"


def test_allowlist_entries_actually_exist() -> None:
    """Every allowlist entry must point at a real file."""
    missing = [rel for rel in _ALLOWLIST if not (SRC_ROOT / rel).exists()]
    assert not missing, f"W547 allowlist references missing files: {missing}"


# ---------------------------------------------------------------------------
# W564 — drift-guard: severity-rank table
# ---------------------------------------------------------------------------
#
# Pre-W564 13+ sites owned their own ``_SEVERITY_RANK`` / ``_SEVERITY_ORDER``
# table. Pattern-3a metric-divergence on ORDER. The canonical
# :func:`roam.output._severity.severity_rank` is the single source of
# truth.
#
# What this drift-guard catches: any dict literal whose KEYS draw from
# the canonical severity vocabulary AND whose VALUES are integers, with
# at least two distinct (key, value) pairs. That shape only appears in
# code that's rolling its own rank table.

_RANK_KEYS = frozenset(
    {
        "critical",
        "error",
        "high",
        "warning",
        "medium",
        "low",
        "info",
        "note",
        "unknown",
        "CRITICAL",
        "ERROR",
        "HIGH",
        "WARNING",
        "MEDIUM",
        "LOW",
        "INFO",
        "NOTE",
        "UNKNOWN",
    }
)


def _is_rank_table(dict_node: ast.Dict) -> bool:
    """Return True when *dict_node* looks like a severity-rank table.

    The shape that matters:

    * ALL string keys are drawn from :data:`_RANK_KEYS` (no mixed
      non-severity keys — that rules out summary-count dicts like
      ``{"verdict": "...", "critical": 0, "high": 0}`` and threshold
      configs like ``{"warning": 60, "critical": 40, "higher_is_better": True}``);
    * the dict has AT LEAST 2 string-keyed entries with integer values;
    * the integer values are NOT all the same (rules out severity-count
      breakdowns initialised to zero).

    The combination of these three filters gives ~zero false positives
    on the W564 baseline (only the actual rank tables fire).
    """
    severity_int_pairs: list[tuple[str, int]] = []
    has_non_severity_key = False
    has_string_keys = False
    for k, v in zip(dict_node.keys, dict_node.values):
        if not isinstance(k, ast.Constant):
            continue
        if not isinstance(k.value, str):
            continue
        has_string_keys = True
        if k.value not in _RANK_KEYS:
            has_non_severity_key = True
            continue
        if not isinstance(v, ast.Constant):
            continue
        if isinstance(v.value, bool):
            continue
        if not isinstance(v.value, int):
            continue
        severity_int_pairs.append((k.value, v.value))

    if not has_string_keys or has_non_severity_key:
        return False
    if len(severity_int_pairs) < 2:
        return False
    # Severity-count dicts initialise every value to zero — distinct
    # integer values is the rank-table signature.
    distinct_values = {v for _, v in severity_int_pairs}
    return len(distinct_values) >= 2


# Variable / target names that signal a SEVERITY-rank table. Confidence
# and risk rank tables (``_CONF_RANK``, ``risk_order``, ``_RISK_ORDER``,
# ``base_score``) use overlapping vocab — they rank a DIFFERENT concept
# and stay outside the W564 scope.
#
# W640: broadened beyond ``sever`` to also catch ``level_order`` — the
# pre-W640 ``_LEVEL_ORDER`` in ``cmd_alerts.py`` was a severity-rank
# table by shape (keys ⊂ canonical vocab, distinct int values) but the
# name slipped the original ``/sever/i`` filter. The new pattern catches
# any rank-shaped table whose target name encodes "severity" or
# "level_order" — the two attested naming conventions across the
# pre-W640 baseline.
_RANK_NAME_PATTERN = re.compile(r"sever|level_order", re.IGNORECASE)


def _find_inline_rank_tables(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(SRC_ROOT).as_posix()
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if not _is_rank_table(node.value):
            continue
        # Only flag when at least one target name signals "severity".
        target_names: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                target_names.append(target.id)
        if not any(_RANK_NAME_PATTERN.search(n) for n in target_names):
            continue
        hits.append(f"{rel}:{node.lineno}")
    # Annotated assignments (``_SEVERITY_RANK: dict[str, int] = {...}``)
    # are AnnAssign nodes, not Assign — handle separately.
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if not _is_rank_table(node.value):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if not _RANK_NAME_PATTERN.search(node.target.id):
            continue
        hits.append(f"{rel}:{node.lineno}")
    return hits


def test_no_inline_severity_rank_tables() -> None:
    """W564: every severity-rank table must live in roam.output._severity."""
    # Allowlist combines the SARIF-table allowlist and the W564
    # domain-vocab allowlist — both kinds of dict can legitimately live
    # outside the canonical module.
    allowed = set(_ALLOWLIST) | set(_RANK_ALLOWLIST)
    violations: list[str] = []
    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in allowed:
            continue
        violations.extend(_find_inline_rank_tables(path))
    assert not violations, (
        "W564: inline severity-rank table — import severity_rank "
        "from roam.output._severity instead:\n  " + "\n  ".join(violations)
    )


def test_w564_rank_allowlist_entries_actually_exist() -> None:
    """Every W564 rank-allowlist entry must point at a real file."""
    missing = [rel for rel in _RANK_ALLOWLIST if not (SRC_ROOT / rel).exists()]
    assert not missing, f"W564 rank-allowlist references missing files: {missing}"


def test_w564_severity_rank_round_trip() -> None:
    """Every canonical level + every alias resolves to a finite rank."""
    from roam.output._severity import (
        SEVERITY_ALIASES,
        SEVERITY_LEVELS,
        severity_rank,
    )

    # Every canonical level has a non-negative rank.
    for level in SEVERITY_LEVELS:
        assert severity_rank(level) >= 0, f"canonical level {level!r} should have rank >= 0"

    # Every alias resolves to a finite rank. ``unknown`` is a special
    # case: it is a legitimate label (npm-audit / osv emit it when
    # CVSS is missing) but ranks BELOW every defined tier, so a
    # vuln with no CVSS never climbs above a defined ``info`` finding.
    for alias in SEVERITY_ALIASES:
        if alias == "unknown":
            assert severity_rank(alias) == -1, "alias 'unknown' ranks below info (W531 CI-safety lesson)"
        else:
            assert severity_rank(alias) >= 0, f"alias {alias!r} should have rank >= 0"

    # Polarity: higher = worse.
    assert severity_rank("critical") > severity_rank("error")
    assert severity_rank("error") >= severity_rank("warning")
    assert severity_rank("warning") > severity_rank("info")
    # CVSS tiers preserved as distinct ranks (the secrets / vulns
    # filter-semantics contract).
    assert severity_rank("high") > severity_rank("medium")
    assert severity_rank("medium") > severity_rank("low")
    # Unknown collapses below every known tier (W531 CI-safety).
    assert severity_rank("bogus") < severity_rank("info")
    assert severity_rank(None) < severity_rank("info")
    # Case-insensitive.
    assert severity_rank("CRITICAL") == severity_rank("critical")
    assert severity_rank("High") == severity_rank("high")


@pytest.mark.parametrize(
    "rel,expected_marker",
    [
        ("commands/cmd_vulns.py", "to_sarif_level"),
        ("security/taint_engine.py", "validate_severity"),
    ],
)
def test_migrated_sites_import_canonical_module(rel: str, expected_marker: str) -> None:
    """Known migration targets each import from roam.output._severity."""
    path = SRC_ROOT / rel
    text = path.read_text(encoding="utf-8")
    assert "roam.output._severity" in text, f"W547: {rel} should import from roam.output._severity"
    assert expected_marker in text, f"W547: {rel} should reference {expected_marker}"


def test_w548_taint_engine_validates_severity() -> None:
    """Unknown severities in YAML rules raise a UserWarning (W548).

    Note: Python's default ``warnings`` filter de-duplicates messages by
    (file, line, category) — the first run of a process records the
    hit, subsequent calls go silent. We explicitly set ``simplefilter
    ("always")`` to override the dedup and guarantee the warning surfaces
    even when this test runs after other tests in the same session.
    """
    import tempfile
    import warnings

    from roam.security.taint_engine import load_rules

    with tempfile.TemporaryDirectory() as tmp:
        rule_file = Path(tmp) / "bad.yaml"
        rule_file.write_text(
            "id: bad-rule\nseverity: moderate\nsources:\n  - foo\nsinks:\n  - bar\n",
            encoding="utf-8",
        )
        with warnings.catch_warnings(record=True) as caught:
            # "always" overrides the (file, line, category) dedup that
            # otherwise hides the warning when this test runs after a
            # peer test in the same Python process.
            warnings.resetwarnings()
            warnings.simplefilter("always")
            rules = load_rules(tmp)
        assert any("moderate" in str(w.message) for w in caught), (
            f"W548: load_rules should warn on unknown severity, caught={[str(w.message) for w in caught]}"
        )
        # Rule is still loaded — degraded to canonical 'info' fallback
        assert len(rules) == 1
        assert rules[0].severity == "info"


def test_w548_taint_engine_accepts_canonical_severity() -> None:
    """Canonical severities in YAML rules pass without warning."""
    import tempfile
    import warnings

    from roam.security.taint_engine import load_rules

    with tempfile.TemporaryDirectory() as tmp:
        rule_file = Path(tmp) / "good.yaml"
        rule_file.write_text(
            "id: good-rule\nseverity: error\nsources:\n  - foo\nsinks:\n  - bar\n",
            encoding="utf-8",
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            rules = load_rules(tmp)
        sev_warnings = [w for w in caught if "severity" in str(w.message).lower()]
        assert not sev_warnings, f"W548: canonical severity should NOT warn, got {sev_warnings}"
        assert len(rules) == 1
        assert rules[0].severity == "error"


# ---------------------------------------------------------------------------
# W565 — severity_to_confidence_level helper
# ---------------------------------------------------------------------------


def test_w565_helper_default_mapping() -> None:
    """Default vocab maps CVSS+SARIF tiers onto high/medium/low."""
    from roam.output._severity import severity_to_confidence_level

    # Refactor-target tier.
    assert severity_to_confidence_level("critical") == "high"
    assert severity_to_confidence_level("error") == "high"
    assert severity_to_confidence_level("high") == "high"
    # Monitor tier.
    assert severity_to_confidence_level("warning") == "medium"
    assert severity_to_confidence_level("medium") == "medium"
    # Informational tier (incl. unknown-by-spec ``unknown`` alias).
    assert severity_to_confidence_level("info") == "low"
    assert severity_to_confidence_level("low") == "low"
    assert severity_to_confidence_level("note") == "low"
    assert severity_to_confidence_level("unknown") == "low"


def test_w565_helper_edge_cases() -> None:
    """Empty / None / bogus labels collapse to the CI-safe floor."""
    from roam.output._severity import severity_to_confidence_level

    assert severity_to_confidence_level(None) == "low"
    assert severity_to_confidence_level("") == "low"
    assert severity_to_confidence_level("bogus") == "low"
    # Custom default overrides the floor.
    assert severity_to_confidence_level(None, default="high") == "high"
    assert severity_to_confidence_level("bogus", default="medium") == "medium"


def test_w565_helper_case_insensitive_and_overrides() -> None:
    """Uppercase labels match; overrides win over the default table."""
    from roam.output._severity import severity_to_confidence_level

    # Case-insensitive matches the case-insensitive contract documented
    # in :func:`normalize_severity`.
    assert severity_to_confidence_level("CRITICAL") == "high"
    assert severity_to_confidence_level("Medium") == "medium"
    # Override flips a single mapping without re-rolling the full
    # default; case-insensitive on the override key too.
    assert severity_to_confidence_level("warning", overrides={"warning": "high"}) == "high"
    assert severity_to_confidence_level("WARNING", overrides={"warning": "high"}) == "high"
    # Other keys untouched by the override.
    assert severity_to_confidence_level("critical", overrides={"warning": "high"}) == "high"


def test_w565_helper_replaces_complexity_table() -> None:
    """Helper output equals the pre-W565 ``_COMPLEXITY_SEVERITY_TO_CONFIDENCE``."""
    from roam.output._severity import severity_to_confidence_level

    # The pre-W565 table; the helper must reproduce it byte-identically
    # for the four labels cmd_complexity actually emits.
    pre_w565 = {
        "CRITICAL": "high",
        "HIGH": "high",
        "MEDIUM": "medium",
        "LOW": "low",
    }
    for label, expected in pre_w565.items():
        assert severity_to_confidence_level(label) == expected, (
            f"W565: helper must reproduce pre-W565 complexity mapping for {label!r}"
        )


def test_w565_helper_replaces_smell_table() -> None:
    """Helper output equals the pre-W565 ``_SMELL_SEVERITY_TO_CONFIDENCE``."""
    from roam.output._severity import severity_to_confidence_level

    pre_w565 = {
        "critical": "high",
        "warning": "medium",
        "info": "low",
    }
    for label, expected in pre_w565.items():
        assert severity_to_confidence_level(label) == expected, (
            f"W565: helper must reproduce pre-W565 smells mapping for {label!r}"
        )


# ---------------------------------------------------------------------------
# W566 — severity_breakdown helper
# ---------------------------------------------------------------------------


def test_w566_breakdown_default_vocab() -> None:
    """Default vocab (CVSS 5-tier + unknown) buckets cmd_vulns inputs."""
    from roam.output._severity import severity_breakdown

    vulns = [
        {"severity": "critical"},
        {"severity": "CRITICAL"},  # case-insensitive
        {"severity": "high"},
        {"severity": "medium"},
        {"severity": "low"},
        {"severity": "bogus"},  # routes to ``unknown``
        {"severity": None},  # routes to ``unknown``
    ]
    result = severity_breakdown(vulns)
    assert result == {
        "critical": 2,
        "high": 1,
        "medium": 1,
        "low": 1,
        "unknown": 2,
    }


def test_w566_breakdown_empty_and_drop_zero() -> None:
    """Empty input + drop_zero=True returns empty dict; False keeps vocab."""
    from roam.output._severity import severity_breakdown

    assert severity_breakdown([]) == {}
    assert severity_breakdown([], drop_zero=False) == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "unknown": 0,
    }


def test_w566_breakdown_custom_vocab_and_callable_key() -> None:
    """Custom vocab + callable extractor + unknown-bucket=None drops misses."""
    from roam.output._severity import severity_breakdown

    class Item:
        def __init__(self, sev: str) -> None:
            self.sev = sev

    # Callable extractor over attribute-style items; 3-tier vocab; drop
    # unknown labels silently (cmd_secrets pre-W566 contract).
    items = [Item("high"), Item("medium"), Item("medium"), Item("bogus")]
    result = severity_breakdown(
        items,
        key=lambda i: i.sev,
        vocab=("high", "medium", "low"),
        unknown_bucket=None,
        drop_zero=False,
    )
    assert result == {"high": 1, "medium": 2, "low": 0}


def test_w566_breakdown_reproduces_vulns_contract() -> None:
    """W566 helper output equals the pre-W566 ``cmd_vulns._severity_breakdown``."""
    from roam.commands.cmd_vulns import _severity_breakdown
    from roam.output._severity import severity_breakdown

    vulns = [
        {"severity": "critical"},
        {"severity": "high"},
        {"severity": "high"},
        {"severity": "medium"},
        {"severity": "BOGUS"},
        {"severity": None},
    ]
    # cmd_vulns._severity_breakdown is the W566 thin wrapper — proves
    # the call site stayed byte-identical.
    assert _severity_breakdown(vulns) == severity_breakdown(vulns)


def test_w566_breakdown_reproduces_critique_contract() -> None:
    """W566 helper reproduces the critique aggregator's zero-padded 4-tier."""
    from roam.critique.aggregator import aggregate
    from roam.critique.checks import Finding

    # Same fixture as :func:`test_critique.test_empty_findings_clean_verdict`.
    result = aggregate([])
    assert result["severity_breakdown"] == {
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
    }
    # Non-empty input — preserves the original ordering of the vocab.
    result2 = aggregate(
        [
            Finding("c1", "high", "h1", "...", {}),
            Finding("c1", "high", "h2", "...", {}),
            Finding("c1", "medium", "m1", "...", {}),
        ]
    )
    assert result2["severity_breakdown"] == {
        "high": 2,
        "medium": 1,
        "low": 0,
        "info": 0,
    }


# ---------------------------------------------------------------------------
# W596 — drift-guard: confidence-LEVEL rank table
# ---------------------------------------------------------------------------
#
# Pre-W596 14 sites owned their own confidence-LEVEL rank table — same
# Pattern-3a metric-divergence symptom as W547 / W564, but on the
# confidence-LEVEL axis (``high`` / ``medium`` / ``low`` [+ ``unknown``])
# instead of severity. The canonical
# :func:`roam.output.confidence.confidence_level_rank` is the single
# source of truth.
#
# What this drift-guard catches: any dict literal whose KEYS draw from
# the confidence-LEVEL vocabulary AND whose VALUES are integers, with
# at least two distinct (key, value) pairs, assigned to a target whose
# name signals "confidence" / "conf".

_CONFIDENCE_LEVEL_KEYS = frozenset(
    {
        "high",
        "medium",
        "low",
        "unknown",
        "HIGH",
        "MEDIUM",
        "LOW",
        "UNKNOWN",
    }
)


# W596 sites permitted to keep a local confidence-LEVEL rank table.
# Each entry MUST cite the reason inline.
_W596_CONFIDENCE_ALLOWLIST: dict[str, str] = {
    # The canonical module owns the contract.
    "output/confidence.py": "canonical module — defines _CONFIDENCE_LEVEL_RANK",
}


def _is_confidence_level_rank_table(dict_node: ast.Dict) -> bool:
    """Return True when *dict_node* looks like a confidence-LEVEL rank table.

    Mirror of :func:`_is_rank_table` but scoped to the confidence-LEVEL
    vocabulary. ALL string keys must come from
    :data:`_CONFIDENCE_LEVEL_KEYS`, at least 2 string-keyed integer
    pairs, and not all values identical (rules out zero-padded
    breakdown buckets).
    """
    pairs: list[tuple[str, int]] = []
    has_non_conf_key = False
    has_string_keys = False
    for k, v in zip(dict_node.keys, dict_node.values):
        if not isinstance(k, ast.Constant):
            continue
        if not isinstance(k.value, str):
            continue
        has_string_keys = True
        if k.value not in _CONFIDENCE_LEVEL_KEYS:
            has_non_conf_key = True
            continue
        if not isinstance(v, ast.Constant):
            continue
        if isinstance(v.value, bool):
            continue
        if not isinstance(v.value, int):
            continue
        pairs.append((k.value, v.value))

    if not has_string_keys or has_non_conf_key:
        return False
    if len(pairs) < 2:
        return False
    distinct_values = {v for _, v in pairs}
    return len(distinct_values) >= 2


# Variable / target names that signal a CONFIDENCE-rank table. Matches
# ``_CONF_RANK`` / ``_CONF_ORDER`` / ``_conf_order`` / ``_confidence_order``
# / ``conf_rank`` / ``_CONFIDENCE_RANK`` / ``ranks`` / bare ``rank``.
# ``risk_order`` / ``risk_rank`` are NOT matched — those rank a
# different concept (risk severity) that happens to share the
# ``high/medium/low`` vocabulary; they stay outside W596 scope.
_CONFIDENCE_NAME_PATTERN = re.compile(
    r"^conf|conf_|_conf|confidence|^ranks?$",
    re.IGNORECASE,
)


def _find_inline_confidence_rank_tables(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(SRC_ROOT).as_posix()
    hits: list[str] = []

    def _name_signals_confidence(name: str) -> bool:
        return bool(_CONFIDENCE_NAME_PATTERN.search(name))

    # Assignments: ``_CONF_RANK = {...}`` / ``rank = {...}``
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if not _is_confidence_level_rank_table(node.value):
            continue
        target_names: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                target_names.append(target.id)
        if not any(_name_signals_confidence(n) for n in target_names):
            continue
        hits.append(f"{rel}:{node.lineno}")

    # Annotated assignments: ``_CONF_RANK: dict[str, int] = {...}``
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if not _is_confidence_level_rank_table(node.value):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if not _name_signals_confidence(node.target.id):
            continue
        hits.append(f"{rel}:{node.lineno}")

    return hits


def test_no_inline_confidence_level_rank_tables() -> None:
    """W596: every confidence-LEVEL rank table must live in roam.output.confidence."""
    allowed = set(_W596_CONFIDENCE_ALLOWLIST)
    violations: list[str] = []
    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in allowed:
            continue
        violations.extend(_find_inline_confidence_rank_tables(path))
    assert not violations, (
        "W596: inline confidence-LEVEL rank table — import "
        "confidence_level_rank from roam.output.confidence instead:\n  " + "\n  ".join(violations)
    )


def test_w596_allowlist_entries_actually_exist() -> None:
    """Every W596 confidence-allowlist entry must point at a real file."""
    missing = [rel for rel in _W596_CONFIDENCE_ALLOWLIST if not (SRC_ROOT / rel).exists()]
    assert not missing, f"W596 confidence-allowlist references missing files: {missing}"


def test_w596_confidence_level_rank_round_trip() -> None:
    """Canonical levels + ``unknown`` all resolve to a finite rank."""
    from roam.output.confidence import (
        CONFIDENCE_LEVELS,
        confidence_level_rank,
    )

    # Canonical triple: higher = more confident.
    assert confidence_level_rank("high") > confidence_level_rank("medium")
    assert confidence_level_rank("medium") > confidence_level_rank("low")
    # ``unknown`` is a known label (cmd_pr_bundle) but ranks BELOW low.
    assert confidence_level_rank("unknown") < confidence_level_rank("low")
    # W634 (post-W596): fail-loud on typos / None by default. Silent
    # bucketing requires explicit ``fallback=-1`` opt-in.
    assert confidence_level_rank("bogus", fallback=-1) < confidence_level_rank("unknown")
    assert confidence_level_rank(None, fallback=-1) < confidence_level_rank("unknown")
    # Case-insensitive.
    assert confidence_level_rank("HIGH") == confidence_level_rank("high")
    assert confidence_level_rank("High") == confidence_level_rank("high")
    # Every canonical level resolves to a finite rank.
    for level in CONFIDENCE_LEVELS:
        assert confidence_level_rank(level) >= 0, f"canonical level {level!r} must have rank >= 0"


def test_w596_confidence_level_rank_reproduces_legacy_polarity() -> None:
    """Pre-W596 ``high=3, medium=2, low=1`` polarity preserved verbatim."""
    from roam.output.confidence import confidence_level_rank

    # Match the pre-W596 ``{high:3, medium:2, low:1}`` table that 7 of
    # the 14 sites used (sort-key polarity, higher = more confident).
    assert confidence_level_rank("high") == 3
    assert confidence_level_rank("medium") == 2
    assert confidence_level_rank("low") == 1
    # cmd_pr_bundle's pre-W596 ``unknown=0`` extension preserved.
    assert confidence_level_rank("unknown") == 0


@pytest.mark.parametrize(
    "rel",
    [
        "commands/cmd_auth_gaps.py",
        "commands/cmd_causal_graph.py",
        "commands/cmd_idempotency.py",
        "commands/cmd_laws.py",
        "commands/cmd_math.py",
        "commands/cmd_migration_safety.py",
        "commands/cmd_missing_index.py",
        "commands/cmd_n1.py",
        "commands/cmd_orphan_routes.py",
        "commands/cmd_over_fetch.py",
        "commands/cmd_pr_bundle.py",
        "commands/cmd_side_effects.py",
        "commands/cmd_tx_boundaries.py",
        "laws/miner.py",
        "world_model/causal_graph.py",
    ],
)
def test_w596_migrated_sites_import_canonical_helper(rel: str) -> None:
    """Each of the 15 W596 migration targets imports confidence_level_rank.

    The task brief listed 14 sites; the drift-guard discovered one
    additional confidence-rank table (cmd_laws.py:251) that was missing
    from the original inventory.
    """
    path = SRC_ROOT / rel
    text = path.read_text(encoding="utf-8")
    assert "confidence_level_rank" in text, f"W596: {rel} should reference confidence_level_rank"
    assert "roam.output.confidence" in text, f"W596: {rel} should import from roam.output.confidence"


# ---------------------------------------------------------------------------
# W631 — drift-guard: risk-LEVEL rank table
# ---------------------------------------------------------------------------
#
# Pre-W631 the contract was implicit. Two confirmed sites owned their own
# risk-rank table on different vocabularies + polarities:
#
#   * cmd_migration_plan.py:107 — 3-tier ``{"low": 0, "medium": 1,
#     "high": 2}`` (lower=safer).
#   * cmd_path_coverage.py:453  — 4-tier ``{"CRITICAL": 0, "HIGH": 1,
#     "MEDIUM": 2, "LOW": 3}`` (lower=worse, CRITICAL first).
#
# Same Pattern-3a metric-divergence symptom as W547 / W564 / W596 but on
# the risk-LEVEL axis. The canonical
# :func:`roam.output.risk.risk_rank` is the single source of truth;
# polarity is "higher = worse" (matching severity_rank).

_RISK_LEVEL_KEYS = frozenset(
    {
        "critical",
        "high",
        "medium",
        "low",
        "moderate",
        "CRITICAL",
        "HIGH",
        "MEDIUM",
        "LOW",
        "MODERATE",
    }
)


# W631 sites permitted to keep a local risk-LEVEL rank table.
# Each entry MUST cite the reason inline.
_W631_RISK_ALLOWLIST: dict[str, str] = {
    # The canonical module owns the contract.
    "output/risk.py": "canonical module — defines _RISK_RANK",
}


def _is_risk_level_rank_table(dict_node: ast.Dict) -> bool:
    """Return True when *dict_node* looks like a risk-LEVEL rank table.

    Mirror of :func:`_is_confidence_level_rank_table` but scoped to
    the risk-LEVEL vocabulary. ALL string keys must come from
    :data:`_RISK_LEVEL_KEYS`, at least 2 string-keyed integer pairs,
    and not all values identical (rules out zero-padded breakdown
    buckets like ``{"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}``).
    """
    pairs: list[tuple[str, int]] = []
    has_non_risk_key = False
    has_string_keys = False
    for k, v in zip(dict_node.keys, dict_node.values):
        if not isinstance(k, ast.Constant):
            continue
        if not isinstance(k.value, str):
            continue
        has_string_keys = True
        if k.value not in _RISK_LEVEL_KEYS:
            has_non_risk_key = True
            continue
        if not isinstance(v, ast.Constant):
            continue
        if isinstance(v.value, bool):
            continue
        if not isinstance(v.value, int):
            continue
        pairs.append((k.value, v.value))

    if not has_string_keys or has_non_risk_key:
        return False
    if len(pairs) < 2:
        return False
    distinct_values = {v for _, v in pairs}
    return len(distinct_values) >= 2


# Variable / target names that signal a RISK-rank table. Matches
# ``risk_order`` / ``_RISK_ORDER`` / ``risk_rank`` / ``_RISK_RANK`` /
# ``_RISK_LEVEL_RANK`` / bare ``risk``. The W596 ``conf*`` /
# ``confidence*`` pattern and W564 ``sever*`` pattern stay out of
# scope.
_RISK_NAME_PATTERN = re.compile(r"risk", re.IGNORECASE)


def _find_inline_risk_rank_tables(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(SRC_ROOT).as_posix()
    hits: list[str] = []

    def _name_signals_risk(name: str) -> bool:
        return bool(_RISK_NAME_PATTERN.search(name))

    # Assignments: ``risk_order = {...}`` / ``_RISK_ORDER = {...}``
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if not _is_risk_level_rank_table(node.value):
            continue
        target_names: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                target_names.append(target.id)
        if not any(_name_signals_risk(n) for n in target_names):
            continue
        hits.append(f"{rel}:{node.lineno}")

    # Annotated assignments: ``_RISK_RANK: dict[str, int] = {...}``
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if not _is_risk_level_rank_table(node.value):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if not _name_signals_risk(node.target.id):
            continue
        hits.append(f"{rel}:{node.lineno}")

    return hits


def test_no_inline_risk_level_rank_tables() -> None:
    """W631: every risk-LEVEL rank table must live in roam.output.risk."""
    allowed = set(_W631_RISK_ALLOWLIST)
    violations: list[str] = []
    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in allowed:
            continue
        violations.extend(_find_inline_risk_rank_tables(path))
    assert not violations, (
        "W631: inline risk-LEVEL rank table — import risk_rank from "
        "roam.output.risk instead:\n  " + "\n  ".join(violations)
    )


def test_w631_allowlist_entries_actually_exist() -> None:
    """Every W631 risk-allowlist entry must point at a real file."""
    missing = [rel for rel in _W631_RISK_ALLOWLIST if not (SRC_ROOT / rel).exists()]
    assert not missing, f"W631 risk-allowlist references missing files: {missing}"


def test_w631_risk_rank_round_trip() -> None:
    """Canonical levels + ``moderate`` alias all resolve to a finite rank."""
    from roam.output.risk import RISK_LEVELS, risk_rank

    # Canonical 4-tier: higher = worse.
    assert risk_rank("critical") > risk_rank("high")
    assert risk_rank("high") > risk_rank("medium")
    assert risk_rank("medium") > risk_rank("low")
    # ``moderate`` alias resolves to ``medium``.
    assert risk_rank("moderate") == risk_rank("medium")
    assert risk_rank("MODERATE") == risk_rank("medium")
    # Typos / None collapse below ``low`` (W531 CI-safety lesson).
    assert risk_rank("bogus") < risk_rank("low")
    assert risk_rank(None) < risk_rank("low")
    # Case-insensitive.
    assert risk_rank("CRITICAL") == risk_rank("critical")
    assert risk_rank("High") == risk_rank("high")
    # Every canonical level resolves to a finite rank >= 0.
    for level in RISK_LEVELS:
        assert risk_rank(level) >= 0, f"canonical level {level!r} must have rank >= 0"


def test_w631_risk_rank_legacy_polarity_preserved() -> None:
    """cmd_migration_plan + cmd_path_coverage pre-W631 sort orders preserved.

    Both pre-W631 sites used the lower-is-first polarity. The
    canonical helper uses higher-is-worse polarity; callers that want
    the legacy polarity negate the rank. This test pins the
    byte-identical sort order for both sites.
    """
    from roam.output.risk import risk_rank

    # cmd_path_coverage: CRITICAL > HIGH > MEDIUM > LOW > unknown.
    samples = ["LOW", "CRITICAL", "MEDIUM", "bogus", "HIGH"]
    samples.sort(key=lambda r: -risk_rank(r))
    assert samples == ["CRITICAL", "HIGH", "MEDIUM", "LOW", "bogus"], f"W631: path_coverage polarity drift: {samples}"

    # cmd_migration_plan: low > medium > high > unknown (lower risk
    # first; unknown last). Uses the order_key trick to push unknowns
    # to the tail.
    def _order_key(risk: str) -> int:
        r = risk_rank(risk)
        return r if r >= 0 else 999

    samples2 = ["high", "low", "medium", "bogus"]
    samples2.sort(key=_order_key)
    assert samples2 == ["low", "medium", "high", "bogus"], f"W631: migration_plan polarity drift: {samples2}"


@pytest.mark.parametrize(
    "rel",
    [
        "commands/cmd_migration_plan.py",
        "commands/cmd_path_coverage.py",
    ],
)
def test_w631_migrated_sites_import_canonical_helper(rel: str) -> None:
    """Each W631 migration target imports risk_rank from roam.output.risk."""
    path = SRC_ROOT / rel
    text = path.read_text(encoding="utf-8")
    assert "risk_rank" in text, f"W631: {rel} should reference risk_rank"
    assert "roam.output.risk" in text, f"W631: {rel} should import from roam.output.risk"
