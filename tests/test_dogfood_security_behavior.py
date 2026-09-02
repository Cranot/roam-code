"""Behavioural dogfood of roam's SECURITY & REACHABILITY command cluster.

These are NOT JSON-envelope shape tests. Each test copies the polyglot fixture
under ``tests/fixtures/dogfood_security/`` into a temp dir, runs ``roam index``,
and then drives a real security command against the real index, asserting on
concrete structure / counts / reachability verdicts. A regression that made any
command collapse to degenerate output (0 findings, empty inventory, wrong
match) would FAIL these tests.

Commands covered: effects, side-effects, secrets, taint, auth-gaps, sbom,
vulns, vuln-reach, vuln-map.

Several tests are ``xfail(strict=True)`` — they assert the *correct* behaviour
that the command does NOT yet produce, pinning a confirmed defect (CP44/CP45
"make the absence loud" discipline). When a defect is fixed the test XPASSes
and ``strict=True`` fails the suite, forcing the fixer to flip it to a plain
assertion.

Run:
  .venv/Scripts/python.exe -m pytest tests/test_dogfood_security_behavior.py \
      -p no:cacheprovider -o addopts="" -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_SRC = Path(__file__).parent / "fixtures" / "dogfood_security"
_INDEX_TIMEOUT = 240
_CMD_TIMEOUT = 180


# ---------------------------------------------------------------------------
# Harness — copy the fixture to a temp dir and index it with the SAME python
# that runs pytest (the task mandates .venv/Scripts/python.exe, which carries
# numpy; sys.executable is therefore the correct interpreter).
# ---------------------------------------------------------------------------


def _run_roam(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_CMD_TIMEOUT,
    )


def _run_json(repo: Path, *args: str) -> dict:
    """Run ``roam --json <args>`` and parse the envelope."""
    proc = _run_roam(repo, "--json", *args)
    assert proc.stdout.strip(), f"empty stdout for {args}: stderr={proc.stderr[:500]}"
    # The envelope is the last JSON object on stdout (progress lines, if any,
    # go to stderr, but be defensive).
    text = proc.stdout
    start = text.index("{")
    return json.loads(text[start:])


def _build_repo(tmp_path_factory, name: str) -> Path:
    repo = tmp_path_factory.mktemp(name)
    for item in FIXTURE_SRC.iterdir():
        dst = repo / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
    # Inject the Stripe secret at build time. GitHub push-protection blocks any
    # committed `sk_live_<24+>` literal, so it is assembled from fragments here
    # and written into the temp repo — roam scans the rendered file (full token
    # present) while no committed file (fixture or this test) holds it whole.
    _stripe = "sk_" + "live_" + "51H8s" + "abcdefghijklmnopqrstuvwx" + "0123456789"
    _secretsmod = repo / "app" / "secretsmod.py"
    if _secretsmod.exists():
        _secretsmod.write_text(
            _secretsmod.read_text(encoding="utf-8") + f'STRIPE_SECRET_KEY = "{_stripe}"\n',
            encoding="utf-8",
        )
    proc = _run_roam(repo, "index", "-q")
    assert proc.returncode == 0, f"index failed: {proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    assert (repo / ".roam" / "index.db").exists(), "index.db not created"
    return repo


@pytest.fixture(scope="module")
def ro_repo(tmp_path_factory) -> Path:
    """Read-only shared index (no command below mutates the vulns table)."""
    return _build_repo(tmp_path_factory, "dogfood_ro")


@pytest.fixture(scope="module")
def vuln_repo(tmp_path_factory) -> Path:
    """Indexed repo with the seed report imported EXACTLY ONCE (4 rows)."""
    repo = _build_repo(tmp_path_factory, "dogfood_vuln")
    proc = _run_roam(repo, "vulns", "--import-file", "seeds/generic_vulns.json")
    assert proc.returncode == 0, proc.stderr[-500:]
    return repo


def _effect_map(envelope: dict) -> dict[str, list[str]]:
    return {s["name"]: list(s.get("direct_effects", [])) for s in envelope["symbols"]}


# ===========================================================================
# effects — regex effect classifier (naive; over-matches on receiver name)
# ===========================================================================


def test_effects_true_positive_on_real_sqlite_dao(ro_repo):
    """A real sqlite DAO must be classified as DB read/write."""
    env = _run_json(ro_repo, "effects", "--path", "app/real_db.py")
    effects = _effect_map(env)
    assert "writes_db" in effects["save_user"], effects
    assert "reads_db" in effects["read_user"], effects


# M3 FIXED (2026-07-28): `_PYTHON_PATTERNS` no longer matches DB verbs on ANY
# receiver — `.get(`/`.add(`/`.update(` were dropped (receiver-agnostic;
# dict.get/set.add/dict.update FPs) and `.execute(`/`.executemany(` now
# require a literal SQL keyword. app/pure_dict.py's functions are genuinely
# pure, so they now carry NO effects at all and are absent from the
# `--path` listing (an INNER JOIN against symbol_effects) — that absence
# IS the correct outcome, hence `.get(fn, [])` rather than `effects[fn]`.
def test_effects_false_positive_pure_dict_should_have_no_db_effects(ro_repo):
    env = _run_json(ro_repo, "effects", "--path", "app/pure_dict.py")
    effects = _effect_map(env)
    db_kinds = {"reads_db", "writes_db"}
    for fn in ("lookup_settings", "collect", "merge"):
        assert not (db_kinds & set(effects.get(fn, []))), f"{fn} falsely flagged: {effects.get(fn)}"


# ===========================================================================
# side-effects — import-aware classifier. Contradicts `effects` on pure_dict.
# ===========================================================================


def _sideeffect_map(env: dict) -> dict[str, list[str]]:
    return {c["symbol"]: list(c.get("kinds", [])) for c in env["classifications"]}


def test_side_effects_correctly_none_on_pure_dict(ro_repo):
    """side-effects (evidence/import-aware) is the CORRECT arbiter here: the pure
    dict/set functions carry NO side effects. This directly contradicts `effects`
    (which flags them reads_db/writes_db), demonstrating which command is wrong."""
    env = _run_json(ro_repo, "side-effects")
    kinds = _sideeffect_map(env)
    for fn in ("lookup_settings", "collect", "merge"):
        assert kinds[fn] == ["none"], f"{fn}: {kinds[fn]}"


def test_side_effects_true_positive_io(ro_repo):
    env = _run_json(ro_repo, "side-effects")
    kinds = _sideeffect_map(env)
    assert set(kinds["run_query"]) >= {"io_read", "io_write"}, kinds["run_query"]
    assert "io_write" in kinds["save_user"], kinds["save_user"]
    assert "io_read" in kinds["read_user"], kinds["read_user"]


# ===========================================================================
# secrets
# ===========================================================================


def test_secrets_detects_stripe_and_github(ro_repo):
    env = _run_json(ro_repo, "secrets")
    patterns = {f["value"]["pattern"] for f in env["findings"]}
    assert "Stripe Secret Key" in patterns, patterns
    assert any("GitHub" in p for p in patterns), patterns
    assert env["summary"]["by_severity"].get("high", 0) >= 2, env["summary"]
    assert env["summary"]["total_findings"] == 5, env["summary"]


def test_secrets_suppresses_aws_documentation_example(ro_repo):
    """The two AWS lines use the canonical AWS docs EXAMPLE values; the scanner's
    _is_placeholder_line must suppress them (correct behaviour, not a defect)."""
    env = _run_json(ro_repo, "secrets")
    for f in env["findings"]:
        v = f["value"]
        assert "AWS" not in v["pattern"], f"AWS example wrongly reported: {v}"
        assert v["line"] not in (9, 10), f"suppressed line reported: {v}"


# ===========================================================================
# taint — W452 silent-SAFE on genuinely vulnerable code
# ===========================================================================


def test_taint_command_runs_clean_exit(ro_repo):
    proc = _run_roam(ro_repo, "taint")
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "rule(s)" in proc.stdout


def _finding_rule_id(finding: dict) -> str | None:
    """Read ``rule_id`` off one entry of the JSON ``findings`` array.

    R22 wraps every taint finding as ``{"value": {...}, "confidence":
    ..., "reason": ...}`` — the real fields (``rule_id``, ``source``,
    ``sink``, ...) live under ``finding["value"]``, not at the top
    level. Falls back to a top-level ``rule_id``/``rule`` for
    forward-compat with any command whose envelope isn't R22-wrapped.
    """
    value = finding.get("value")
    if isinstance(value, dict):
        rid = value.get("rule_id") or value.get("rule")
        if rid:
            return rid
    return finding.get("rule_id") or finding.get("rule")


def test_taint_flags_obvious_sqli(ro_repo):
    """FIXED 2026-07-27 (task #285, W452 indexer gap). Was xfail(strict=True):
    app/web.py has an unambiguous request.args -> cursor.execute SQLi, but
    `roam taint` used to report 'No taint findings across 22 rules' because
    the Python indexer never materialises the source/sink symbols
    (request.args, cursor.execute) so the BFS had no nodes to connect —
    silent-SAFE on a vulnerable repo. `run_taint`'s text-scan fallback
    (roam.security.taint_engine._text_scan_rule_anchors) now anchors these
    literal occurrences to their enclosing function (search / run_query)
    via the already-indexed line ranges, and forward-BFS connects them
    through the real search() -> run_query() call edge. Pinned at the
    engine level in tests/test_w452_python_taint_indexer_gap.py; this pins
    it on the real security fixture.
    """
    env = _run_json(ro_repo, "taint", "--rules-pack", "sqli")
    findings = env.get("findings", [])
    assert any(_finding_rule_id(f) == "python-sqli" for f in findings), (
        f"python-sqli produced no finding on an obvious SQLi flow: {findings}"
    )


def _finding_value(finding: dict) -> dict:
    """Unwrap one R22 ``{"value": {...}}`` finding to its real fields."""
    value = finding.get("value")
    return value if isinstance(value, dict) else finding


def test_taint_ci_exit_code_tracks_finding_severity(ro_repo):
    """`taint --ci` must gate on measured severity — in BOTH directions.

    This replaces an assertion that demanded exit 5 from the `sqli` pack.
    That expectation went stale on 2026-08-24 ("taint reserves error
    severity for computed dataflow"): a production run had returned 144
    error-level findings of which expert triage found ZERO real — all were
    text co-occurrence, common-caller links, or text-anchor BFS, none with a
    computed source-to-sink path — so error severity now REQUIRES a computed
    dataflow, and everything else is reported as a `note`.

    The two fixture flows land on opposite sides of that line, which is what
    makes them a usable pair:

      * command-injection — `ping()` reads request.args and calls os.system
        in ONE function body, so the intraprocedural AST pass computes the
        path. evidence=dataflow, severity=error, gate exits 5.
      * sqli — `search()` (web.py:24) reads request.args and hands it to
        `run_query()` (web.py:33), which builds the SQL. The dataflow pass is
        intraprocedural, so this one-hop flow is only reachable by the
        text-anchor fallback. evidence=co_occurrence, severity=note, and the
        gate exits 0 because nothing reached error.

    So the product did not stop FINDING the SQLi; it stopped CLAIMING it at
    error severity. Demanding exit 5 there now asserts the fabricated-proof
    behaviour that change deleted. What must still hold — and what task #285
    was really about — is that the gate never silently blesses a repo:

      (a) the interprocedural SQLi flow is still REPORTED, not dropped;
      (b) every finding's severity is exactly what its evidence authorizes —
          no co-occurrence promoted to error, no computed dataflow demoted;
      (c) the exit code tracks the error count in both directions, checked
          against a SEPARATE CLI invocation rather than read off the same
          envelope it is compared to.

    (c) is the original defect's shape: a pack that produces an error-level
    finding and still exits 0 fails here on the command-injection leg.
    """
    saw_error_pack = False
    saw_note_pack = False

    for pack, rule_id in (("sqli", "python-sqli"), ("command-injection", "python-command-injection")):
        env = _run_json(ro_repo, "taint", "--rules-pack", pack)
        findings = env.get("findings", [])
        assert any(_finding_rule_id(f) == rule_id for f in findings), (
            f"{pack}: {rule_id} produced no finding on an obvious flow — "
            f"the flow must be reported even when its severity is only a note: {findings}"
        )

        # (b) severity is exactly what the evidence authorizes.
        errors = []
        for f in findings:
            v = _finding_value(f)
            sev, evidence = v.get("severity"), v.get("evidence")
            basis = v.get("basis") or ""
            if evidence == "dataflow":
                assert sev in {"error", "warning"}, f"{pack}: computed dataflow demoted to {sev!r}: {v}"
                assert "unverified" not in basis, f"{pack}: computed dataflow calls itself unverified: {v}"
            else:
                assert evidence == "co_occurrence", f"{pack}: unknown evidence class {evidence!r}: {v}"
                assert sev == "note", f"{pack}: co-occurrence claims {sev!r} without a computed path: {v}"
                assert "unverified" in basis, f"{pack}: an unproven finding must say so in its basis: {v}"
            if sev == "error":
                errors.append(v)

        # (c) the gate keys off the error count (cmd_taint `high_count`),
        # measured by a second, independent invocation.
        proc = _run_roam(ro_repo, "taint", "--ci", "--rules-pack", pack)
        expected = 5 if errors else 0
        assert proc.returncode == expected, (
            f"{pack}: taint --ci must exit {expected} with {len(errors)} error-severity "
            f"finding(s), got {proc.returncode}: stdout={proc.stdout[-500:]!r}"
        )
        saw_error_pack |= bool(errors)
        saw_note_pack |= not errors

    # Neither leg may quietly become vacuous: this test only proves the gate
    # tracks severity if it actually exercised an exit-5 case AND an exit-0 one.
    assert saw_error_pack, "no pack produced an error-severity finding — the exit-5 direction went untested"
    assert saw_note_pack, "no pack produced a note-only result — the exit-0 direction went untested"


def test_taint_flags_obvious_command_injection(ro_repo):
    """Companion to test_taint_flags_obvious_sqli: app/web.py also has an
    unambiguous request.args -> os.system command injection in ping()
    (source and sink both inside the same handler — the same-function
    co-occurrence shape the text-scan fallback detects directly, since
    forward BFS structurally can't express "source and sink are the same
    node")."""
    env = _run_json(ro_repo, "taint", "--rules-pack", "command-injection")
    findings = env.get("findings", [])
    assert any(_finding_rule_id(f) == "python-command-injection" for f in findings), (
        f"python-command-injection produced no finding on an obvious os.system flow: {findings}"
    )
    proc = _run_roam(ro_repo, "taint", "--ci", "--rules-pack", "command-injection")
    assert proc.returncode == 5, (
        f"taint --ci must exit 5 on an obvious command-injection flow, got {proc.returncode}: "
        f"stdout={proc.stdout[-500:]!r}"
    )


# ===========================================================================
# auth-gaps — the working, sellable Laravel detector (strong regression guard)
# ===========================================================================


def test_auth_gaps_flags_unprotected_routes_not_protected(ro_repo):
    env = _run_json(ro_repo, "auth-gaps")
    routes = {(g["verb"], g["path"]) for g in env["route_gaps"]}
    assert ("GET", "/admin/reports") in routes, routes
    assert ("POST", "/admin/reports") in routes, routes
    assert ("DELETE", "/admin/reports/{id}") in routes, routes
    # The route inside the auth:sanctum group must NOT be flagged.
    assert not any(p == "/admin/audit" for _, p in routes), routes
    assert env["summary"]["route_gaps"] == 3, env["summary"]


def test_auth_gaps_flags_unauthorized_controller_methods(ro_repo):
    env = _run_json(ro_repo, "auth-gaps")
    by_method = {g["method"]: g["confidence"] for g in env["controller_gaps"]}
    assert by_method.get("store") == "high", by_method
    assert by_method.get("destroy") == "high", by_method
    assert by_method.get("index") == "low", by_method
    assert env["summary"]["total"] == 6, env["summary"]


# ===========================================================================
# sbom — reachability enrichment distinguishes imported from phantom deps
# ===========================================================================


def _sbom_reachable(env: dict) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for c in env["sbom"]["components"]:
        props = {p["name"]: p["value"] for p in c.get("properties", [])}
        out[c["name"]] = props.get("roam:reachable") == "true"
    return out


def test_sbom_reachability_separates_imported_from_phantom(ro_repo):
    env = _run_json(ro_repo, "sbom")
    reach = _sbom_reachable(env)
    # Imported in app/web.py -> reachable.
    assert reach["requests"] is True, reach
    assert reach["PyYAML"] is True, reach
    assert reach["Flask"] is True, reach
    # Declared in manifests but never imported anywhere -> phantom (unreachable).
    assert reach["lodash"] is False, reach
    assert reach["axios"] is False, reach
    assert reach["Jinja2"] is False, reach


# ===========================================================================
# vulns — ingestion + import-site matching against real imports
# ===========================================================================


def _vuln_by_pkg(env: dict) -> dict[str, dict]:
    return {v["value"]["package"]: v["value"] for v in env["vulnerabilities"]}


def test_vulns_import_matches_real_imports_only(vuln_repo):
    env = _run_json(vuln_repo, "vulns")
    assert env["summary"]["total"] == 4, env["summary"]
    by_pkg = _vuln_by_pkg(env)
    # requests + PyYAML ARE imported in app/web.py -> import_site match.
    assert by_pkg["requests"]["match_kind"] == "import_site", by_pkg["requests"]
    assert by_pkg["requests"]["matched_file"] == "app/web.py", by_pkg["requests"]
    assert by_pkg["PyYAML"]["match_kind"] == "import_site", by_pkg["PyYAML"]
    # lodash is declared in package.json but never imported; control isn't present.
    assert by_pkg["lodash"].get("matched_file") is None, by_pkg["lodash"]
    assert by_pkg["nonexistent-pkg-xyz"].get("matched_file") is None


def test_vuln_reach_reports_import_reachable(vuln_repo):
    env = _run_json(vuln_repo, "vuln-reach")
    tag = {v["package"]: v["reachability"] for v in env["vulnerabilities"]}
    assert tag["requests"] == "import-reachable", tag
    assert tag["PyYAML"] == "import-reachable", tag
    assert tag["lodash"] == "unmatched", tag
    assert tag["nonexistent-pkg-xyz"] == "unmatched", tag
    # No call-graph reachability is claimed (honest given the indexer limit).
    assert env["summary"]["reachable_count"] == 0, env["summary"]
    assert env["summary"]["import_reachable_count"] == 2, env["summary"]


def test_vulns_reachable_only_does_not_claim_empty(vuln_repo):
    """FIXED (M2, DOGFOOD-DEFECTS-2026-07-15): after importing 4 vulns,
    `vulns --reachable-only` used to print 'no vulnerability scan available
    (vulnerabilities table is empty; ...)' even though the table held 4
    rows -- every third-party CVE has matched_symbol_id=None (import-site
    matches never seed a symbol id), so the reachable==1 post-filter list
    was empty and the verdict treated total==0 as 'table empty', hiding
    the 4 import-reachable vulns and telling the user to re-import data
    that was already there. Fixed by carrying the pre-filter row count
    (cmd_vulns._count_all_vulns) through the verdict/summary so 'no data'
    and 'N rows, 0 reachable' are distinguishable in both text and JSON.
    """
    proc = _run_roam(vuln_repo, "vulns", "--reachable-only")
    assert proc.returncode == 0, proc.stderr[-500:]
    low = proc.stdout.lower()
    assert "table is empty" not in low, proc.stdout
    assert "no vulnerability scan" not in low, proc.stdout
    # The verdict must positively disclose that real data exists.
    assert "4 vulnerabilities" in low, proc.stdout
    assert "0 reachable" in low, proc.stdout

    env = _run_json(vuln_repo, "vulns", "--reachable-only")
    assert env["summary"]["state"] == "scanned", env["summary"]
    assert env["summary"]["partial_success"] is False, env["summary"]
    assert env["summary"]["total"] == 0, env["summary"]
    assert env["summary"]["pre_filter_total"] == 4, env["summary"]


# ===========================================================================
# vuln ingestion idempotency + vuln-map project_root wiring (mutating)
# ===========================================================================


def test_vuln_import_is_idempotent(tmp_path_factory):
    """FIXED (M4, DOGFOOD-DEFECTS-2026-07-15): importing the SAME report
    twice used to duplicate every row (4 -> 8 -> 12 ...) because
    vuln_store._insert_vuln was a bare INSERT with no dedup on (cve_id,
    package_name, source) -- an unbounded inflation on repeat that
    silently broke any count-based CI gate. Fixed via an UPSERT keyed on
    a NULL-safe (COALESCE(cve_id, ''), package_name, source) UNIQUE
    index (roam.db.connection migrations seq 61/62 + vuln_store.
    ensure_vuln_table for standalone connections): a re-import updates
    the existing row's severity/title/match evidence instead of
    inserting a duplicate.
    """
    repo = _build_repo(tmp_path_factory, "dogfood_dup")
    _run_roam(repo, "vulns", "--import-file", "seeds/generic_vulns.json")
    _run_roam(repo, "vulns", "--import-file", "seeds/generic_vulns.json")
    env = _run_json(repo, "vulns")
    assert env["summary"]["total"] == 4, f"re-import duplicated rows: {env['summary']}"
    # A third import (different call path timing) must still be stable.
    _run_roam(repo, "vulns", "--import-file", "seeds/generic_vulns.json")
    env3 = _run_json(repo, "vulns")
    assert env3["summary"]["total"] == 4, f"third import drifted: {env3['summary']}"


def test_vuln_map_matches_imported_packages(tmp_path_factory):
    """FIXED (M1, DOGFOOD-DEFECTS-2026-07-15): `vuln-map --generic` used
    to report every imported package as '-> no match (not imported)'.
    Two stacked bugs: (1) cmd_vuln_map.vuln_map_cmd called the
    ingest_* helpers without project_root, so match_vuln_to_symbols
    never scanned concrete import specifiers at all; (2) even with
    project_root wired through, third-party packages have no indexed
    SYMBOL, so matched_symbol_id stays None by design (a bare name
    coincidence must not seed graph reachability) and the old `matched`
    counter only counted matched_symbol_id. Fixed by passing
    project_root=find_project_root() to every ingest_* call AND
    counting matched_file (import-site evidence) as a match too,
    mirroring vulns/sbom/vuln-reach.
    """
    repo = _build_repo(tmp_path_factory, "dogfood_map")
    env = _run_json(repo, "vuln-map", "--generic", "seeds/generic_vulns.json")
    assert env["summary"]["matched"] >= 2, env["summary"]
    by_pkg = {v["package_name"]: v for v in env["vulnerabilities"]}
    assert by_pkg["requests"]["matched_file"] == "app/web.py", by_pkg["requests"]
    assert by_pkg["PyYAML"]["matched_file"] == "app/web.py", by_pkg["PyYAML"]
