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


@pytest.mark.xfail(
    strict=True,
    reason="CONFIRMED DEFECT (effects): the regex classifier flags dict.get()/set.add()/"
    "dict.update() on ANY receiver as reads_db/writes_db. app/pure_dict.py has NO "
    "database at all, yet every function is reported with a DB effect. src/roam/"
    "analysis/effects.py _PYTHON_PATTERNS: r'\\.get\\s*\\(' -> READS_DB, "
    "r'\\.add\\s*\\(' / r'\\.update\\s*\\(' -> WRITES_DB. side-effects (a smarter, "
    "import-aware classifier) correctly calls these 'none' — see the companion test.",
)
def test_effects_false_positive_pure_dict_should_have_no_db_effects(ro_repo):
    env = _run_json(ro_repo, "effects", "--path", "app/pure_dict.py")
    effects = _effect_map(env)
    db_kinds = {"reads_db", "writes_db"}
    for fn in ("lookup_settings", "collect", "merge"):
        assert not (db_kinds & set(effects[fn])), f"{fn} falsely flagged: {effects[fn]}"


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


@pytest.mark.xfail(
    strict=True,
    reason="CONFIRMED DEFECT (taint / W452 indexer gap): app/web.py has an unambiguous "
    "request.args -> cursor.execute SQLi and request.args -> os.system command "
    "injection, yet `roam taint` reports 'No taint findings across 22 rules' and "
    "`taint --ci` exits 0. The Python indexer never materialises the source/sink "
    "symbols (request.args, cursor.execute, os.system) so the BFS has no nodes to "
    "connect. This is a silent-SAFE on a vulnerable repo — a CI gate on `taint --ci` "
    "would pass it. Pinned in tests/test_w452_python_taint_indexer_gap.py at the "
    "engine level; this pins it on the real security fixture.",
)
def test_taint_flags_obvious_sqli(ro_repo):
    env = _run_json(ro_repo, "taint", "--rules-pack", "sqli")
    findings = env.get("findings", [])
    assert any((f.get("rule_id") or f.get("rule")) == "python-sqli" for f in findings), (
        f"python-sqli produced no finding on an obvious SQLi flow: {findings}"
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


@pytest.mark.xfail(
    strict=True,
    reason="CONFIRMED DEFECT (vulns --reachable-only): after importing 4 vulns, "
    "`vulns --reachable-only` prints 'no vulnerability scan available "
    "(vulnerabilities table is empty; run roam vulns --import-file ...)'. The table "
    "is NOT empty. src/roam/commands/cmd_vulns.py: _query_vulns filters to "
    "reachable==1 (line ~1024); every third-party CVE has matched_symbol_id=None "
    "(import-site matches never seed a symbol id), so the post-filter list is empty "
    "and _build_verdict_str treats total==0 as 'table empty', hiding the 4 "
    "import-reachable vulns and telling the user to re-import. Should report "
    "'4 vulnerabilities, 0 reachable' instead.",
)
def test_vulns_reachable_only_does_not_claim_empty(vuln_repo):
    proc = _run_roam(vuln_repo, "vulns", "--reachable-only")
    assert proc.returncode == 0, proc.stderr[-500:]
    low = proc.stdout.lower()
    assert "table is empty" not in low, proc.stdout
    assert "no vulnerability scan" not in low, proc.stdout


# ===========================================================================
# vuln ingestion idempotency + vuln-map project_root wiring (mutating)
# ===========================================================================


@pytest.mark.xfail(
    strict=True,
    reason="CONFIRMED DEFECT (vuln ingestion not idempotent): importing the SAME "
    "report twice duplicates every row (4 -> 8 -> 12 ...). src/roam/security/"
    "vuln_store.py _insert_vuln does a bare INSERT with no dedup on "
    "(cve_id, package_name, source). The user-facing count inflates unbounded "
    "('8 vulnerabilities' for the same 4), so any count-based CI gate misfires.",
)
def test_vuln_import_is_idempotent(tmp_path_factory):
    repo = _build_repo(tmp_path_factory, "dogfood_dup")
    _run_roam(repo, "vulns", "--import-file", "seeds/generic_vulns.json")
    _run_roam(repo, "vulns", "--import-file", "seeds/generic_vulns.json")
    env = _run_json(repo, "vulns")
    assert env["summary"]["total"] == 4, f"re-import duplicated rows: {env['summary']}"


@pytest.mark.xfail(
    strict=True,
    reason="CONFIRMED DEFECT (vuln-map): `vuln-map --generic` reports imported "
    "packages as '-> no match (not imported)'. Deeper than the missing "
    "project_root: verified 2026-07-15 that find_project_root() resolves and "
    "scan_import_reachability() DOES find the sites (526 for 'click'), yet vuln-map "
    "still reports 0 matched. Root cause: third-party packages have no indexed "
    "SYMBOL, so match_vuln_to_symbols leaves matched_symbol_id=None, and vuln-map "
    "counts only symbol-matches. Fix: count import-site reachability as a match "
    "(matched_file / import-reachable), mirroring vulns/sbom/vuln-reach which "
    "detect the same imports correctly.",
)
def test_vuln_map_matches_imported_packages(tmp_path_factory):
    repo = _build_repo(tmp_path_factory, "dogfood_map")
    env = _run_json(repo, "vuln-map", "--generic", "seeds/generic_vulns.json")
    assert env["summary"]["matched"] >= 2, env["summary"]
