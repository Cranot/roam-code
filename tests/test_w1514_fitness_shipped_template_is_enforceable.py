"""W1514 -- the fitness rules `roam init` writes must be rules, not comments.

`roam init` writes `.roam/fitness.yaml` into every new repository. Both of the
rules it wrote were addressed to a vocabulary no checker reads:

  * `threshold: 30` on the complexity rule. ``_check_cognitive_complexity_metric``
    reads ``max`` and defaults it to 999, so a function MEASURED at cognitive
    complexity 377 PASSED a rule whose own reason string says "Functions above
    30 cognitive complexity need refactoring". Inert.
  * `source:` / `forbidden_target:` on the dependency rule.
    ``_check_dependency_rule`` reads ``from`` / ``to``, both defaulting to
    ``**``, so the rule forbade EVERY edge in the repository. On a
    two-function, zero-defect repo that `roam init` had just created, the
    shipped rule was the named risk driver in `roam preflight`. Universally
    false-positive -- at HIGH confidence, with `config_state: "ok"` and
    `partial_success: false` beside it.

Neither spelling belongs to any roam schema: `.roam-rules.yml` (a different
file, read by pr-analyze) uses ``source_glob`` / ``forbidden_target_glob``, so
the template was a mangled half-copy of a sibling vocabulary.

The guards below come in two halves, and the second half is what keeps the
fix from becoming an outage:

  must-fire   -- the shipped template, run through the real checkers, now
                 FAILS on a cc>30 function and does NOT fire on an intra-src
                 call; the loader names every key nothing reads.
  must-not-fire -- `threshold` is still NOT an alias for `max` (aliasing it
                 would switch a dormant gate ON in every repo that has ever
                 run `roam init`, since the file is only written when absent);
                 unread keys WARN and never become an ERROR row or change an
                 exit code; a one-sided dependency rule is not reported as
                 vacuous; and a clean repo with the new template passes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from roam.commands import cmd_fitness
from roam.commands.cmd_fitness import (
    _RULE_KEYS_BY_TYPE,
    _UNIVERSAL_RULE_KEYS,
    _load_rules_with_status,
    _rule_counts,
    _run_fitness_rules,
    _unread_rule_keys,
    _vacuous_dependency_rule,
)
from roam.commands.cmd_init import _FITNESS_YAML
from roam.db.connection import open_db

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

# The exact template `roam init` wrote before this fix. Kept as a literal so
# the must-not-fire tests below assert against the bytes still sitting in
# every repo that has ever run `roam init` -- the file is written only when
# absent, so those configs are never rewritten by an upgrade.
_LEGACY_FITNESS_YAML = """\
rules:
  - name: No circular imports in core
    type: dependency
    source: "src/**"
    forbidden_target: "tests/**"
    reason: "Production code should not import test modules"
  - name: Complexity threshold
    type: metric
    metric: cognitive_complexity
    threshold: 30
    reason: "Functions above 30 cognitive complexity need refactoring"
"""


def _nested_ifs(depth: int) -> str:
    """A function whose cognitive complexity is far above any sane threshold."""
    lines = ["def gnarly(a):", "    t = 0"]
    for i in range(depth):
        lines.append("    " + "    " * i + f"if a > {i}:")
        lines.append("    " + "    " * (i + 1) + f"t += {i}")
    lines.append("    return t")
    return "\n".join(lines) + "\n"


@pytest.fixture
def fitness_corpus(tmp_path, monkeypatch):
    """One over-threshold function plus a benign intra-src call.

    This is the fixture the shipped template is supposed to have an opinion
    about: `gnarly` must trip the complexity rule, and `caller -> helper` --
    an intra-file call in `src/`, neither circular nor an import of a test
    module -- must trip nothing.
    """
    proj = tmp_path / "w1514_corpus"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "app.py").write_text(
        _nested_ifs(13) + "\n\ndef helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"
    return proj


def _rules_from(yaml_text: str, proj: Path) -> tuple[list[dict], list[str]]:
    """Write *yaml_text* as the project's fitness config and load it."""
    (proj / ".roam").mkdir(exist_ok=True)
    (proj / ".roam" / "fitness.yaml").write_text(yaml_text, encoding="utf-8")
    warnings: list[str] = []
    rules, status = _load_rules_with_status(proj, warnings_out=warnings)
    assert status == "ok", status
    return rules, warnings


def _evaluate(rules: list[dict], proj: Path) -> dict[str, dict]:
    with open_db(readonly=True) as conn:
        rule_results, _violations = _run_fitness_rules(conn, rules)
    return {r["name"]: r for r in rule_results}


# ---------------------------------------------------------------------------
# must-fire -- the shipped template, through the real checkers
# ---------------------------------------------------------------------------


def test_shipped_template_fails_on_an_over_threshold_function(fitness_corpus) -> None:
    """The rule whose reason names 30 must fail a function measuring 377."""
    rules, warnings = _rules_from(_FITNESS_YAML, fitness_corpus)
    assert warnings == [], f"the shipped template must not warn about itself: {warnings}"
    results = _evaluate(rules, fitness_corpus)
    complexity = results["Complexity threshold"]
    assert complexity["status"] == "FAIL", results
    assert complexity["violations"] >= 1


def test_shipped_template_does_not_fire_on_an_intra_src_call(fitness_corpus) -> None:
    """`caller -> helper` is not a production-imports-tests violation."""
    rules, _ = _rules_from(_FITNESS_YAML, fitness_corpus)
    results = _evaluate(rules, fitness_corpus)
    dependency = results["Production code must not import test modules"]
    assert dependency["status"] == "PASS", results
    assert dependency["violations"] == 0


def test_shipped_dependency_rule_still_catches_a_real_test_import(tmp_path, monkeypatch) -> None:
    """The must-fire pair for the dependency half: a real src -> tests edge.

    Without this, "does not fire on an intra-src call" would also be
    satisfied by a rule that fires on nothing at all.
    """
    proj = tmp_path / "w1514_leak"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src").mkdir()
    (proj / "tests").mkdir()
    (proj / "tests" / "helpers.py").write_text("def fixture_user():\n    return 1\n", encoding="utf-8")
    (proj / "src" / "app.py").write_text(
        "from tests.helpers import fixture_user\n\n\ndef boot():\n    return fixture_user()\n",
        encoding="utf-8",
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed:\n{out}"

    rules, _ = _rules_from(_FITNESS_YAML, proj)
    results = _evaluate(rules, proj)
    dependency = results["Production code must not import test modules"]
    assert dependency["status"] == "FAIL", results


def test_legacy_on_disk_config_is_disclosed_key_by_key(fitness_corpus) -> None:
    """The config already on disk everywhere must say what it cannot read."""
    _rules, warnings = _rules_from(_LEGACY_FITNESS_YAML, fitness_corpus)
    blob = "\n".join(warnings)
    assert "'source'" in blob and "'forbidden_target'" in blob, warnings
    assert "'threshold'" in blob, warnings
    assert "read by NOTHING" in blob
    # And the vacuous-dependency disclosure, which is the reason the rule
    # fired on every edge rather than merely doing nothing.
    assert "neither `from` nor `to`" in blob, warnings
    assert "forbids EVERY edge" in blob, warnings


def test_every_shipped_template_key_is_one_a_checker_reads() -> None:
    """Structural guard: neither shipped template may drift out of vocabulary.

    `roam init` and `roam fitness --init` both write a starter config. A key
    nothing reads is what this whole finding is; asserting it here means the
    next edit to either template cannot reintroduce it silently.
    """
    import inspect
    import re

    from roam.commands._yaml_loader import parse_rule_list

    init_source = inspect.getsource(cmd_fitness._init_config)
    body = re.search(r'"""(.*?)""",\s*\n\s*encoding="utf-8"', init_source, re.DOTALL)
    assert body is not None, "could not locate the `roam fitness --init` template literal"

    for label, text in (("roam init", _FITNESS_YAML), ("roam fitness --init", body.group(1))):
        rules = parse_rule_list(text)
        assert rules, f"{label} template parsed to no rules"
        for rule in rules:
            assert rule.get("type") in _RULE_KEYS_BY_TYPE, f"{label}: {rule}"
            unread = _unread_rule_keys(rule)
            assert unread == [], f"{label} writes dead keys {unread} on rule {rule.get('name')!r}"
            assert not _vacuous_dependency_rule(rule), (
                f"{label} writes a dependency rule constraining neither end: {rule}"
            )


# ---------------------------------------------------------------------------
# must-not-fire -- the fixes that would turn this defect into an outage
# ---------------------------------------------------------------------------


def test_threshold_is_not_an_alias_for_max(fitness_corpus) -> None:
    """`threshold: 30` must STILL be inert. This is the forbidden fix.

    `roam init` writes `.roam/fitness.yaml` only when it is absent, so every
    repo that has ever run it still carries `threshold: 30` on disk. Teaching
    the checker to read `threshold` would switch a dormant cc>30 gate ON in
    all of them at upgrade time, with no config change by the user -- 377 vs
    30 on a fifteen-line file here, and `roam fitness` exits 1. The fix is the
    template plus the load-time warning, never a silent alias.
    """
    rules, warnings = _rules_from(_LEGACY_FITNESS_YAML, fitness_corpus)
    results = _evaluate(rules, fitness_corpus)
    assert results["Complexity threshold"]["status"] == "PASS", (
        "an on-disk `threshold` key must not have become a live gate at upgrade time"
    )
    assert any("'threshold'" in w for w in warnings), "the inert key must be DISCLOSED even though it is not honoured"


def test_unread_keys_warn_and_never_produce_an_error_row(fitness_corpus) -> None:
    """Documentation keys must warn, not refuse.

    Hand-written configs legitimately carry `owner` / `ticket` / `since`.
    Refusing them would fail configs that work today, and an ERROR row would
    change the exit code (`_finish_fitness` exits 1 when `errored > 0`).
    """
    text = (
        "rules:\n"
        '  - name: "Complexity"\n'
        "    type: metric\n"
        "    metric: cognitive_complexity\n"
        "    max: 1000\n"
        '    owner: "platform-team"\n'
        '    ticket: "ARCH-14"\n'
        '    since: "2026-01-01"\n'
    )
    rules, warnings = _rules_from(text, fitness_corpus)
    results = _evaluate(rules, fitness_corpus)
    _passed, _failed, errored = _rule_counts(list(results.values()))
    assert errored == 0, "an unread key must never become an ERROR row"
    assert results["Complexity"]["status"] == "PASS", "the rule must still be evaluated normally"
    blob = "\n".join(warnings)
    assert "'owner'" in blob and "'ticket'" in blob and "'since'" in blob, warnings


def test_a_working_rule_with_no_extra_keys_warns_about_nothing(fitness_corpus) -> None:
    """No new noise on a config that already used the right vocabulary."""
    text = (
        "rules:\n"
        '  - name: "Handlers must not touch the db"\n'
        "    type: dependency\n"
        '    from: "src/handlers/**"\n'
        '    to: "src/db/**"\n'
        "    allow: false\n"
        '    reason: "layering"\n'
        '    link: "https://example.invalid/adr-1"\n'
    )
    _rules, warnings = _rules_from(text, fitness_corpus)
    assert warnings == [], warnings


def test_one_sided_dependency_rule_is_not_reported_as_vacuous() -> None:
    """ "Nothing anywhere may reach this" is a legitimate leaf rule.

    Only the both-absent case forbids every edge; narrowing the `**` default
    globally would break rules that work today.
    """
    assert not _vacuous_dependency_rule({"type": "dependency", "to": "src/secrets/**"})
    assert not _vacuous_dependency_rule({"type": "dependency", "from": "src/handlers/**"})
    assert _vacuous_dependency_rule({"type": "dependency", "name": "x"})
    # An `allow: true` rule with no globs is an allow-list, not a ban.
    assert not _vacuous_dependency_rule({"type": "dependency", "allow": True})


def test_unknown_type_is_not_double_reported(fitness_corpus) -> None:
    """An unenforceable `type` gets ONE actionable warning, not a key dump."""
    text = 'rules:\n  - name: "Typo"\n    type: dependancy\n    from: "src/**"\n    to: "tests/**"\n'
    _rules, warnings = _rules_from(text, fitness_corpus)
    assert len(warnings) == 1, warnings
    assert "unknown rule type" in warnings[0]
    assert "read by NOTHING" not in warnings[0]


def test_a_rule_with_no_name_does_not_raise(fitness_corpus) -> None:
    """Every checker must tolerate a missing `name`, as its siblings do."""
    text = 'rules:\n  - type: dependency\n    from: "src/**"\n    to: "src/**"\n    allow: false\n'
    rules, _warnings = _rules_from(text, fitness_corpus)
    with open_db(readonly=True) as conn:
        rule_results, violations = _run_fitness_rules(conn, rules)
    assert rule_results[0]["name"] == "unnamed"
    assert all(v["rule"] == "unnamed" for v in violations), violations


def test_universal_keys_cover_every_documented_rule_field() -> None:
    """The accepted set must not silently shrink and start warning on `reason`."""
    assert {"name", "type", "reason", "link"} <= _UNIVERSAL_RULE_KEYS
    for rtype, keys in _RULE_KEYS_BY_TYPE.items():
        assert keys, rtype
        assert not (keys & _UNIVERSAL_RULE_KEYS), f"{rtype} duplicates a universal key"
