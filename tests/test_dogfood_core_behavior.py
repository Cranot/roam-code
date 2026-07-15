"""Dogfood behavioral tests for the CORE-ANALYSIS & GRAPH command cluster.

These build small, deterministic *real-code* fixtures (Laravel/Vue + Python),
index them with the actual ``roam`` CLI into an isolated tmp ``.roam`` (the main
repo index is never touched), and assert the real structure/counts a detector
emits. They are regression tripwires: a command that regresses to *degenerate*
(all-zero verdict labelled "high"), *over-firing* (flagging live/consumed/stdlib
code), or *wrong-direction* (TP suppressed / TN flagged) output fails here.

The ``xfail(strict=True)`` tests document CONFIRMED defects discovered in the
13.9.x dogfood pass. Each is written in the *correct-behaviour* direction, so it
fails today and flips to a hard failure (XPASS) the moment the defect is fixed --
alerting the maintainer to drop the marker.

Run:
  .venv/Scripts/python.exe -m pytest tests/test_dogfood_core_behavior.py \
      -p no:cacheprovider -o addopts="" -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

ROAM = [sys.executable, "-m", "roam"]
MAIN_REPO = repo_root()


# --------------------------------------------------------------------------- #
# Harness helpers
# --------------------------------------------------------------------------- #
def _env() -> dict:
    e = os.environ.copy()
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["GIT_AUTHOR_NAME"] = e["GIT_COMMITTER_NAME"] = "dogfood"
    e["GIT_AUTHOR_EMAIL"] = e["GIT_COMMITTER_EMAIL"] = "dogfood@example.com"
    return e


def run_roam(repo: Path, *args: str, detail: bool = False, as_json: bool = False):
    cmd = list(ROAM)
    if detail:
        cmd.append("--detail")
    if as_json:
        cmd.append("--json")
    cmd += list(args)
    return subprocess.run(
        cmd,
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(),
        timeout=300,
    )


def run_json(repo: Path, *args: str, detail: bool = False):
    p = run_roam(repo, *args, detail=detail, as_json=True)
    i = p.stdout.find("{")
    assert i != -1, f"no JSON emitted by {args}:\nSTDOUT={p.stdout[:600]}\nSTDERR={p.stderr[:600]}"
    return json.loads(p.stdout[i:]), p


def _git(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True, env=_env())


def _commit(repo: Path, msg: str = "c"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)


def build_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(root, "init", "-q")
    _commit(root, "init")
    idx = run_roam(root, "index")
    assert idx.returncode == 0, f"index failed:\n{idx.stdout}\n{idx.stderr}"
    return root


# --------------------------------------------------------------------------- #
# Fixture corpora
# --------------------------------------------------------------------------- #
# A minimal but structurally real Laravel + Vue app. TP and TN cases use
# DISTINCT routes/tables so both live in one indexed repo without cross-masking.
LARAVEL_FILES: dict[str, str] = {
    "routes/api.php": (
        "<?php\n"
        "// TP orphan: no frontend consumer references /api/legacy-export\n"
        "Route::get('/api/legacy-export', [ExportController::class, 'index']);\n"
        "// TN orphan: /api/invoices IS referenced by frontend/InvoicesPage.vue\n"
        "Route::get('/api/invoices', [InvoiceController::class, 'index']);\n"
        "// TP auth-gap: unprotected admin route\n"
        "Route::get('/admin/reports', [ReportController::class, 'index']);\n"
        "// TN auth-gap: route inside an auth middleware group\n"
        "Route::middleware(['auth:sanctum'])->group(function () {\n"
        "    Route::get('/admin/settings', [SettingsController::class, 'index']);\n"
        "});\n"
    ),
    "frontend/InvoicesPage.vue": (
        "<!-- consumes the invoices endpoint -->\n<script>const endpoint = '/api/invoices';</script>\n"
    ),
    "app/Models/Order.php": (
        "<?php\n"
        "class Order extends Model\n"
        "{\n"
        "    protected $table = 'orders';\n"
        "    protected $fillable = ['id','account_id','total','status','notes'];\n"
        "    public function recent()\n"
        "    {\n"
        "        return Order::query()->where('account_id', 42)->paginate();\n"
        "    }\n"
        "}\n"
    ),
    "app/Models/Customer.php": (
        "<?php\n"
        "class Customer extends Model\n"
        "{\n"
        "    protected $table = 'customers';\n"
        "    protected $hidden = ['secret_token'];\n"
        "    public function recent()\n"
        "    {\n"
        "        return Customer::query()->where('region_id', 7)->paginate();\n"
        "    }\n"
        "}\n"
    ),
    "app/Http/Controllers/OrderController.php": (
        "<?php\n"
        "class OrderController extends Controller\n"
        "{\n"
        "    // TP: unguarded eager load -- with('items') has no column selection\n"
        "    public function index()\n"
        "    {\n"
        "        return Order::query()->with('items')->paginate();\n"
        "    }\n"
        "}\n"
    ),
    "app/Http/Controllers/CustomerController.php": (
        "<?php\n"
        "class CustomerController extends Controller\n"
        "{\n"
        "    // TN: guarded eager load -- with('orders:cols') selects columns\n"
        "    public function index()\n"
        "    {\n"
        "        return Customer::query()->with('orders:id,customer_id,total')->paginate();\n"
        "    }\n"
        "}\n"
    ),
    "database/migrations/2024_01_01_000000_create_orders_table.php": (
        "<?php\n"
        "Schema::create('orders', function ($table) {\n"
        "    $table->id();\n"
        "    $table->unsignedBigInteger('account_id');\n"
        "    $table->integer('total');\n"
        "});\n"
    ),
    "database/migrations/2024_01_02_000000_create_customers_table.php": (
        "<?php\n"
        "Schema::create('customers', function ($table) {\n"
        "    $table->id();\n"
        "    $table->unsignedBigInteger('region_id');\n"
        "    $table->index('region_id');\n"
        "    $table->string('name');\n"
        "});\n"
    ),
    "database/migrations/2024_01_03_000000_unsafe_ops.php": (
        "<?php\n"
        "class UnsafeOps\n"
        "{\n"
        "    public function up()\n"
        "    {\n"
        "        Schema::drop('legacy_orders');\n"
        "        Schema::table('orders', function ($table) {\n"
        "            $table->dropColumn('obsolete_col');\n"
        "        });\n"
        "    }\n"
        "}\n"
    ),
}


@pytest.fixture(scope="module")
def laravel_repo(tmp_path_factory) -> Path:
    return build_repo(tmp_path_factory.mktemp("laravel_app"), LARAVEL_FILES)


@pytest.fixture(scope="module")
def py_repo(tmp_path_factory) -> Path:
    # used_helper() is called by entry(); never_called() has no consumer.
    return build_repo(
        tmp_path_factory.mktemp("py_app"),
        {
            "mod.py": (
                "def entry():\n"
                "    return used_helper()\n\n"
                "def used_helper():\n"
                "    return 1\n\n"
                "def never_called():\n"
                "    return 42\n"
            )
        },
    )


@pytest.fixture(scope="module")
def yaml_import_repo(tmp_path_factory) -> Path:
    # real.py imports stdlib (json/os) -> must resolve.
    # ci.yml embeds a python snippet that imports stdlib json inside a run block.
    return build_repo(
        tmp_path_factory.mktemp("yaml_app"),
        {
            "real.py": "import json\nimport os\n\ndef go():\n    return json.dumps({})\n",
            ".github/workflows/ci.yml": (
                "name: ci\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: |\n"
                '          python -c "\n'
                "          import json, os, sys\n"
                "          print(json.dumps({}))\n"
                '          "\n'
            ),
        },
    )


@pytest.fixture(scope="module")
def empty_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("empty_app")
    _git(root, "init", "-q")
    _commit(root, "init")  # empty commit, no files
    idx = run_roam(root, "index")
    assert idx.returncode == 0, f"index failed on empty repo:\n{idx.stdout}\n{idx.stderr}"
    return root


@pytest.fixture(scope="module")
def cochange_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("cochange_app")
    _git(root, "init", "-q")
    for i in (1, 2, 3):  # DOC_A + DOC_B co-change exactly 3 times, never apart
        (root / "DOC_A.md").write_text("\n".join(f"a{j}" for j in range(i)) + "\n", encoding="utf-8")
        (root / "DOC_B.md").write_text("\n".join(f"b{j}" for j in range(i)) + "\n", encoding="utf-8")
        _commit(root, f"c{i}")
    (root / "other.py").write_text("x = 1\n", encoding="utf-8")
    _commit(root, "other")
    idx = run_roam(root, "index")
    assert idx.returncode == 0, f"index failed:\n{idx.stdout}\n{idx.stderr}"
    return root


# --------------------------------------------------------------------------- #
# Laravel detectors (13.9.0) -- both directions: TP fires, TN suppressed
# --------------------------------------------------------------------------- #
def test_orphan_routes_tp_fires_and_consumed_route_suppressed(laravel_repo):
    d, p = run_json(laravel_repo, "orphan-routes")
    assert p.returncode == 0
    paths = {o["path"] for o in d["orphans"]}
    # TP: route with no frontend consumer is flagged.
    assert "/api/legacy-export" in paths
    assert "/admin/reports" in paths
    # TN: route referenced by a .vue consumer must NOT be an orphan.
    assert "/api/invoices" not in paths, f"consumed route over-fired as orphan: {paths}"
    s = d["summary"]
    assert s["routes_total"] == 4
    assert s["routes_with_consumers"] == 1
    assert s["orphans_found"] == 3


def test_auth_gaps_tp_fires_and_auth_group_route_suppressed(laravel_repo):
    d, p = run_json(laravel_repo, "auth-gaps")
    assert p.returncode == 0
    flagged = {r["path"] for r in d["route_gaps"]}
    # TP: unprotected routes are flagged.
    assert {"/api/legacy-export", "/admin/reports"} <= flagged
    # TN: a route inside an auth:sanctum middleware group must NOT be flagged.
    assert "/admin/settings" not in flagged, f"auth-group route over-fired: {flagged}"
    assert d["route_gaps"] and len(d["route_gaps"]) == 3


def test_missing_index_tp_fires_and_indexed_column_suppressed(laravel_repo):
    d, p = run_json(laravel_repo, "missing-index")
    assert p.returncode == 0
    findings = d.get("findings") or []
    tables = {f["value"]["table"] for f in findings}
    cols = {c for f in findings for c in f["value"]["columns"]}
    # TP: filter on an unindexed column fires.
    assert "orders" in tables
    assert "account_id" in cols
    # TN: filter on an indexed column (customers.region_id) must be clean.
    assert "customers" not in tables, f"indexed-column filter over-fired: {findings}"
    assert d["summary"]["indexes_found"] >= 2
    assert d["summary"]["migrations_scanned"] == 3


def test_over_fetch_unguarded_relation_fires_guarded_is_advisory(laravel_repo):
    d, p = run_json(laravel_repo, "over-fetch")
    assert p.returncode == 0
    eps = {e["controller"]: e for e in d.get("endpoint_findings", [])}
    assert eps["OrderController"]["state"] == "UNGUARDED_RELATION"  # TP: real leak
    assert eps["OrderController"]["severity"] == "H"
    assert eps["CustomerController"]["state"] == "GUARDED_RELATION"  # TN: already guarded
    s = d["summary"]
    assert s["unguarded_relation_count"] == 1
    assert s["guarded_relation_count"] == 1
    assert s["real_leak_count"] == 1


def test_migration_safety_flags_unsafe_ops_not_guarded_creates(laravel_repo):
    d, p = run_json(laravel_repo, "migration-safety")
    assert p.returncode == 0
    findings = d.get("findings") or []
    assert d["summary"]["total"] == 3
    cats = {f["category"] for f in findings}
    assert {"unsafe_drop", "drop_column_without_check", "missing_down"} <= cats
    # every issue is in the unsafe migration; the two guarded create_* migrations are clean.
    assert all(f["file"].replace("\\", "/").endswith("2024_01_03_000000_unsafe_ops.php") for f in findings), (
        f"safe create migration wrongly flagged: {[f['file'] for f in findings]}"
    )


# --------------------------------------------------------------------------- #
# Core-analysis correctness on controlled Python corpus
# --------------------------------------------------------------------------- #
def test_dead_flags_uncalled_not_called_symbol(py_repo):
    d, p = run_json(py_repo, "dead", detail=True)
    assert p.returncode == 0
    names = {
        (x.get("value") or x).get("name") for x in (d.get("high_confidence") or []) + (d.get("low_confidence") or [])
    }
    # never_called has no consumer -> dead. used_helper is called by entry() -> NOT dead.
    assert "never_called" in names
    assert "used_helper" not in names, f"dead over-fired on a called symbol: {names}"


# --------------------------------------------------------------------------- #
# Crash-safety on an empty corpus (adversarial: no files, no symbols)
# --------------------------------------------------------------------------- #
EMPTY_SAFE_COMMANDS = [
    ("dead",),
    ("clones",),
    ("duplicates",),
    ("coupling",),
    ("dark-matter",),
    ("cycles",),
    ("complexity",),
    ("health",),
    ("debt",),
    ("stats",),
    ("bus-factor",),
    ("dev-profile",),
    ("congestion",),
    ("verify-imports",),
    ("orphan-imports",),
    ("orphan-routes",),
    ("missing-index",),
    ("over-fetch",),
    ("migration-safety",),
    ("auth-gaps",),
    ("api",),
    ("hotspots",),
    ("findings", "count"),
]


@pytest.mark.parametrize("args", EMPTY_SAFE_COMMANDS, ids=lambda a: "-".join(a))
def test_core_commands_do_not_crash_on_empty_corpus(empty_repo, args):
    p = run_roam(empty_repo, *args)
    combined = p.stdout + p.stderr
    assert "Traceback (most recent call last)" not in combined, f"{args} crashed:\n{combined[:800]}"
    assert p.returncode == 0, f"{args} exited {p.returncode}:\n{combined[:800]}"


# --------------------------------------------------------------------------- #
# verify-imports resolves stdlib in real .py (baseline must hold)
# --------------------------------------------------------------------------- #
def test_verify_imports_resolves_stdlib_in_python_file(yaml_import_repo):
    d, _ = run_json(yaml_import_repo, "verify-imports", detail=True)
    unresolved = [i for i in d.get("imports", []) if i.get("status") != "resolved"]
    # stdlib imports inside a genuine .py file must resolve.
    py_unresolved = [i for i in unresolved if str(i.get("file", "")).endswith(".py")]
    assert py_unresolved == [], f"stdlib in a .py wrongly unresolved: {py_unresolved}"


# --------------------------------------------------------------------------- #
# CONFIRMED DEFECTS (strict xfail -> flips to XPASS/failure when fixed)
# --------------------------------------------------------------------------- #
@pytest.mark.xfail(
    reason="DEFECT: verify-imports flags Python stdlib imports embedded in YAML as "
    "unresolved. _scan_import_entry gates the stdlib skip on is_py (host .py), so "
    "an `import json` inside a workflow run-block is reported unresolved (33% of the "
    "roam repo's own verify-imports findings are this FP class). "
    "Fix: src/roam/commands/cmd_verify_imports.py:_scan_import_entry -- apply the "
    "_is_stdlib_module skip regardless of host-file language (or stop extracting "
    "Python imports from non-Python hosts).",
    strict=True,
)
def test_verify_imports_should_not_flag_stdlib_in_yaml(yaml_import_repo):
    d, _ = run_json(yaml_import_repo, "verify-imports", detail=True)
    unresolved_names = {i.get("name") for i in d.get("imports", []) if i.get("status") != "resolved"}
    assert "json" not in unresolved_names, "stdlib module 'json' embedded in a YAML snippet was reported unresolved"


@pytest.mark.xfail(
    reason="DEFECT: debt labels a clean corpus 'high debt' from mean_debt alone, "
    "ignoring the structural signal counts. A 1-file repo with 0 cycles / 0 god "
    "components / 0 hotspots is reported as 'high debt: 0 cycle files, 0 god "
    "components, 0 hotspots' because mean_debt (0.467, driven only by 2 dead exports) "
    "> 0.3. Fix: src/roam/commands/cmd_debt.py:~720 -- the 0.1/0.3 mean_debt "
    "thresholds do not scale to tiny corpora and the label ignores signal counts.",
    strict=True,
)
def test_debt_should_not_be_high_when_no_structural_signals(py_repo):
    d, _ = run_json(py_repo, "debt")
    verdict = (d.get("summary", {}).get("verdict") or "").lower()
    signals_zero = "0 cycle files" in verdict and "0 god components" in verdict and "0 hotspots" in verdict
    assert not (signals_zero and "high debt" in verdict), f"degenerate verdict: {verdict!r}"


@pytest.mark.xfail(
    reason="DEFECT: dark-matter labels minimum-support pairs 'high' risk. Two files "
    "that co-changed exactly 3 times (min-cochanges default) and never apart get "
    "NPMI=1.0 / lift=870 and sort to the top; on the roam repo 28/30 top pairs have "
    "cochange_count==3 with category=UNKNOWN yet risk_level='high'. Fix: "
    "src/roam/commands/cmd_dark_matter.py -- add support-weighted shrinkage or raise "
    "the effective min-support so n=3 co-adds (docs/fixtures) do not dominate as 'high'.",
    strict=True,
)
def test_dark_matter_should_not_high_risk_a_3_cochange_pair(cochange_repo):
    d, _ = run_json(cochange_repo, "dark-matter")
    pairs = d.get("dark_matter_pairs") or []
    low_support_high = [
        p for p in pairs if p.get("cochange_count") == 3 and d.get("summary", {}).get("risk_level_canonical") == "high"
    ]
    assert not low_support_high, (
        f"3-co-change pair surfaced as high-risk dark matter: "
        f"{[(p['file_a'], p['file_b'], p['cochange_count']) for p in low_support_high]}"
    )


@pytest.mark.xfail(
    reason="DEFECT/UX-TRAP: `findings show` rejects the id `findings list` shows. The "
    "text `list` prints the subject_id (e.g. 10888) and the show error hint says 'run "
    "`roam findings list` to discover valid ids', but show requires the finding_id_str "
    "(e.g. dead:export:ec75fae120fc) which text-mode list never prints. A user copies "
    "the visible id into show and gets exit 2 'not found'. Fix: cmd_findings show "
    "should accept subject_id, or list text should surface finding_id_str.",
    strict=True,
)
def test_findings_show_accepts_the_id_list_displays():
    d, p = run_json(MAIN_REPO, "findings", "list")
    findings = d.get("findings") or []
    if not findings:
        pytest.skip("main-repo findings registry empty; nothing to probe")
    subject_id = str(findings[0]["subject_id"])
    shown = run_roam(MAIN_REPO, "findings", "show", subject_id)
    assert shown.returncode == 0, (
        f"subject_id {subject_id} shown by `findings list` is not accepted by "
        f"`findings show` (exit {shown.returncode}): {shown.stdout[:200]}"
    )


# --------------------------------------------------------------------------- #
# findings registry coherence (read-only, main-repo index)
# --------------------------------------------------------------------------- #
def test_findings_show_resolves_the_stable_finding_id():
    """PASS baseline: the *documented* id (finding_id_str) always resolves via show."""
    d, _ = run_json(MAIN_REPO, "findings", "list")
    findings = d.get("findings") or []
    if not findings:
        pytest.skip("main-repo findings registry empty")
    fid = findings[0]["finding_id_str"]
    shown = run_roam(MAIN_REPO, "findings", "show", fid)
    assert shown.returncode == 0, f"finding_id_str {fid} did not resolve: {shown.stdout[:200]}"
    assert fid in shown.stdout


def test_findings_count_total_is_coherent_with_registry():
    """count aggregates >= the (capped) list window and is internally consistent."""
    dc, _ = run_json(MAIN_REPO, "findings", "count")
    dl, _ = run_json(MAIN_REPO, "findings", "list")
    listed = dl.get("findings") or []
    if not listed:
        pytest.skip("main-repo findings registry empty")
    # summary.total (or per-detector rows) must be >= what a single list window shows.
    counts = dc.get("summary", {}) or dc
    total = counts.get("total")
    if total is None:
        # fall back: sum any per-detector integer map present in the envelope
        per = dc.get("counts") or dc.get("by_detector") or {}
        total = sum(v for v in per.values() if isinstance(v, int)) if isinstance(per, dict) else None
    if isinstance(total, int):
        assert total >= len(listed)
