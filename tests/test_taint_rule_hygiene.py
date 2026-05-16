"""W479 hygiene guard for shipped taint rule YAMLs.

Two invariants enforced over every file in
``src/roam/security/taint_rules/``:

1. Static lint: when a rule sets ``qualified_only: true``, every entry in
   ``sources`` / ``sinks`` / ``sanitizers`` must contain a dot. Bare
   names are silent no-ops under the W454/W467 tightened
   ``_symbols_matching`` (see ``src/roam/security/taint_engine.py``) — a
   rule that ships with both ``qualified_only: true`` AND a bare entry
   loses recall without anyone noticing.

2. Load-time warning: ``load_rules`` must emit a
   ``UserWarning`` for any bare entry under ``qualified_only: true``,
   so out-of-tree rule packs (loaded by ``roam vulns`` users via
   ``--rules-dir``) get the same protection the shipped pack does.

Drift guard: extending the rule pack with a new ``qualified_only: true``
rule that re-introduces bare entries will fail
``test_shipped_taint_rules_have_qualified_entries_under_qualified_only``.
The author either qualifies the entry or drops the flag — both are
intentional changes, never silent regressions.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from roam.security.taint_engine import load_rules
from tests._helpers.repo_root import repo_root

_TAINT_RULES_DIR = repo_root() / "src" / "roam" / "security" / "taint_rules"


def _bare_entries(entries: tuple[str, ...]) -> list[str]:
    return [e for e in entries if "." not in str(e)]


def test_taint_rules_dir_exists() -> None:
    """Sanity: the shipped rule pack directory exists and is non-empty."""
    assert _TAINT_RULES_DIR.is_dir(), f"missing rules dir: {_TAINT_RULES_DIR}"
    yamls = list(_TAINT_RULES_DIR.glob("*.yaml"))
    assert yamls, f"no .yaml rules under {_TAINT_RULES_DIR}"


def test_shipped_taint_rules_have_qualified_entries_under_qualified_only() -> None:
    """Every shipped rule with ``qualified_only: true`` has dot-qualified
    entries in sources / sinks / sanitizers. Bare entries would be silent
    no-ops under the W467-tightened matcher.
    """
    # Suppress the load-time warning here — that's exercised by the other
    # test. This test asserts the SHIPPED state of the rule pack.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rules = load_rules(_TAINT_RULES_DIR)
    assert rules, "load_rules returned no rules"

    offenders: list[str] = []
    for rule in rules:
        if not rule.qualified_only:
            continue
        for kind, entries in (
            ("sources", rule.sources),
            ("sinks", rule.sinks),
            ("sanitizers", rule.sanitizers),
        ):
            bare = _bare_entries(entries)
            if bare:
                offenders.append(f"  {rule.rule_id}: {kind}={bare!r}")
    assert not offenders, (
        "Rules ship with qualified_only=true AND bare entries (silent "
        "no-ops under W467). Either qualify the entry or drop "
        "qualified_only:\n" + "\n".join(offenders)
    )


def test_load_rules_warns_on_bare_entry_under_qualified_only(tmp_path: Path) -> None:
    """``load_rules`` emits a ``UserWarning`` mentioning the rule id, the
    entry kind, and the bare name when a rule file sets
    ``qualified_only: true`` AND contains a dot-less entry.
    """
    rule_file = tmp_path / "bad_rule.yaml"
    rule_file.write_text(
        "id: test-bare-under-qualified\n"
        "description: synthetic\n"
        "severity: warning\n"
        "qualified_only: true\n"
        "languages:\n"
        "  - python\n"
        "sources:\n"
        "  - request.args\n"
        "  - inputBare\n"
        "sinks:\n"
        "  - subprocess.run\n"
        "  - bareSink\n"
        "sanitizers:\n"
        "  - shlex.quote\n"
        "  - bareCleaner\n",
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rules = load_rules(tmp_path)

    # Rule still loaded — the lint is advisory.
    assert len(rules) == 1
    assert rules[0].rule_id == "test-bare-under-qualified"
    assert rules[0].qualified_only is True

    messages = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    assert any(
        "test-bare-under-qualified" in m and "qualified_only=true" in m and "inputBare" in m for m in messages
    ), f"expected source warning, got: {messages!r}"
    assert any("test-bare-under-qualified" in m and "bareSink" in m for m in messages), (
        f"expected sink warning, got: {messages!r}"
    )
    assert any("test-bare-under-qualified" in m and "bareCleaner" in m for m in messages), (
        f"expected sanitizer warning, got: {messages!r}"
    )


def test_load_rules_silent_when_qualified_only_off(tmp_path: Path) -> None:
    """Bare entries are perfectly valid when ``qualified_only`` is false
    (the default). No warning should fire.
    """
    rule_file = tmp_path / "ok_rule.yaml"
    rule_file.write_text(
        "id: test-bare-default\n"
        "description: synthetic\n"
        "severity: warning\n"
        "languages:\n"
        "  - python\n"
        "sources:\n"
        "  - request.args\n"
        "  - input\n"
        "sinks:\n"
        "  - subprocess.run\n"
        "  - eval\n"
        "sanitizers:\n"
        "  - shlex.quote\n",
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rules = load_rules(tmp_path)

    assert len(rules) == 1
    qualified_only_warnings = [w for w in caught if "qualified_only=true" in str(w.message)]
    assert not qualified_only_warnings, (
        "load_rules should not warn when qualified_only is unset / false: "
        f"{[str(w.message) for w in qualified_only_warnings]!r}"
    )


def test_load_rules_silent_when_all_entries_dotted(tmp_path: Path) -> None:
    """When ``qualified_only: true`` AND every entry is dot-qualified
    (the discipline both shipped Java rules follow), no warning fires.
    """
    rule_file = tmp_path / "good_qualified_rule.yaml"
    rule_file.write_text(
        "id: test-all-qualified\n"
        "description: synthetic\n"
        "severity: error\n"
        "qualified_only: true\n"
        "languages:\n"
        "  - java\n"
        "sources:\n"
        "  - javax.servlet.http.HttpServletRequest.getParameter\n"
        "sinks:\n"
        "  - java.sql.Statement.executeQuery\n"
        "sanitizers:\n"
        "  - java.sql.PreparedStatement.setString\n",
        encoding="utf-8",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rules = load_rules(tmp_path)

    assert len(rules) == 1
    assert rules[0].qualified_only is True
    qualified_only_warnings = [w for w in caught if "qualified_only=true" in str(w.message)]
    assert not qualified_only_warnings, (
        f"unexpected qualified_only warnings: {[str(w.message) for w in qualified_only_warnings]!r}"
    )


@pytest.mark.parametrize(
    "rule_id_with_qualified_only",
    ["java-sqli", "java-deserialization"],
)
def test_known_qualified_only_rules_intact(rule_id_with_qualified_only: str) -> None:
    """Sentinels: the two shipped rules that opt into qualified_only stay
    that way and stay all-dotted. This is a regression alarm — flipping
    either to qualified_only=false or adding a bare entry should require
    an intentional source edit + a test update.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rules = load_rules(_TAINT_RULES_DIR)
    by_id = {r.rule_id: r for r in rules}
    rule = by_id.get(rule_id_with_qualified_only)
    assert rule is not None, f"expected rule {rule_id_with_qualified_only!r} in shipped pack; have {sorted(by_id)!r}"
    assert rule.qualified_only is True, (
        f"{rule_id_with_qualified_only}: qualified_only flipped to false — intentional change requires test update"
    )
    for kind, entries in (
        ("sources", rule.sources),
        ("sinks", rule.sinks),
        ("sanitizers", rule.sanitizers),
    ):
        assert entries, f"{rule_id_with_qualified_only}: {kind} is empty"
        bare = _bare_entries(entries)
        assert not bare, (
            f"{rule_id_with_qualified_only}: bare {kind}={bare!r} would be no-ops under qualified_only=true"
        )
