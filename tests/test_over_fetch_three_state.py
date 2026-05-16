"""Tests for roam over-fetch 3-state endpoint classification.

The 3 states (per external dogfood feedback on a real "Employee leak"):

  BARE                 — Model::query()->paginate() without ->select() or Resource
  GUARDED_RELATION     — with('rel:col1,col2,...') with explicit column selection
  UNGUARDED_RELATION   — with('rel') eager-load without column selection

These tests build small synthetic Laravel projects and assert that the
classifier returns the expected state + severity for each endpoint shape.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Helpers — synthesize minimal Laravel projects with a single controller
# ---------------------------------------------------------------------------


def _seed_employee_model(proj):
    """Write a wide Employee model so model-level scoring also engages."""
    models = proj / "app" / "Models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "Employee.php").write_text(
        "<?php\nnamespace App\\Models;\n\n"
        "use Illuminate\\Database\\Eloquent\\Model;\n\n"
        "class Employee extends Model {\n"
        "    protected $fillable = [\n"
        "        'first_name', 'last_name', 'email', 'phone', 'address',\n"
        "        'city', 'state', 'zip', 'country', 'date_of_birth',\n"
        "        'social_security', 'national_id', 'tax_id', 'bank_account',\n"
        "        'salary', 'bonus', 'department_id', 'manager_id', 'role',\n"
        "        'hire_date', 'termination_date', 'status', 'photo_url',\n"
        "        'emergency_contact', 'emergency_phone', 'notes',\n"
        "    ];\n"
        "}\n"
    )


def _write_controller(proj, name: str, body: str):
    """Write a controller file with the given method body content.

    The full controller is wrapped — `body` is just the inside of the `index()`
    method. We add a fixed `use App\\Models\\Employee;` import.
    """
    controllers = proj / "app" / "Http" / "Controllers"
    controllers.mkdir(parents=True, exist_ok=True)
    (controllers / f"{name}.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n\n"
        "use App\\Models\\Employee;\n"
        "use App\\Http\\Resources\\EmployeeResource;\n\n"
        f"class {name} extends Controller {{\n"
        "    public function index() {\n"
        f"        {body}\n"
        "    }\n"
        "}\n"
    )


@pytest.fixture
def bare_project(tmp_path):
    """Controller doing Employee::query()->paginate() with no with() and no select()."""
    proj = tmp_path / "bare_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    _seed_employee_model(proj)
    _write_controller(
        proj,
        "BareController",
        "return Employee::query()->paginate(20);",
    )
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def guarded_project(tmp_path):
    """Controller doing Employee::with('manager:id,name')->paginate() — guarded relation."""
    proj = tmp_path / "guarded_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    _seed_employee_model(proj)
    _write_controller(
        proj,
        "GuardedController",
        "return Employee::with('manager:id,name')->select(['id','first_name'])->paginate(20);",
    )
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def unguarded_project(tmp_path):
    """Controller doing Employee::with('manager')->paginate() — unguarded relation."""
    proj = tmp_path / "unguarded_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    _seed_employee_model(proj)
    _write_controller(
        proj,
        "UnguardedController",
        "return Employee::with('manager')->select(['id','first_name'])->paginate(20);",
    )
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def mixed_with_project(tmp_path):
    """Controller doing Employee::with('a:id,name', 'b') — mixed (one guarded, one unguarded)."""
    proj = tmp_path / "mixed_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    _seed_employee_model(proj)
    _write_controller(
        proj,
        "MixedController",
        "return Employee::with('manager:id,name', 'department')->select(['id'])->paginate(20);",
    )
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def api_resource_project(tmp_path):
    """Controller wrapping in EmployeeResource::collection(...) — should NOT be flagged."""
    proj = tmp_path / "api_resource_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    _seed_employee_model(proj)
    # Real Resource file
    res = proj / "app" / "Http" / "Resources"
    res.mkdir(parents=True)
    (res / "EmployeeResource.php").write_text(
        "<?php\nnamespace App\\Http\\Resources;\n\n"
        "use Illuminate\\Http\\Resources\\Json\\JsonResource;\n\n"
        "class EmployeeResource extends JsonResource {\n"
        "    public function toArray($request) {\n"
        "        return ['id' => $this->id, 'name' => $this->first_name];\n"
        "    }\n"
        "}\n"
    )
    _write_controller(
        proj,
        "ResourceController",
        "return EmployeeResource::collection(Employee::query()->paginate(20));",
    )
    git_init(proj)
    index_in_process(proj)
    return proj


@pytest.fixture
def workload_like_project(tmp_path):
    """Synthesized 5-endpoint project matching the real-workload pattern:

    - 1 BARE (full leak)
    - 3 GUARDED_RELATION (already partial — advisory)
    - 1 UNGUARDED_RELATION (the real ~50KB/page leak)
    """
    proj = tmp_path / "union_like"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    _seed_employee_model(proj)

    _write_controller(
        proj,
        "RawListController",  # BARE
        "return Employee::query()->paginate(20);",
    )
    _write_controller(
        proj,
        "AdvanceController",  # GUARDED_RELATION
        "return Employee::with('manager:id,last_name,department_id')->select(['id'])->paginate(20);",
    )
    _write_controller(
        proj,
        "PayrollController",  # GUARDED_RELATION
        "return Employee::with('manager:id,name')->select(['id'])->paginate(20);",
    )
    _write_controller(
        proj,
        "ReportController",  # GUARDED_RELATION
        "return Employee::with('department:id,name')->select(['id'])->paginate(20);",
    )
    _write_controller(
        proj,
        "WorkCardController",  # UNGUARDED_RELATION — the real leak
        "return Employee::with('manager')->select(['id'])->paginate(20);",
    )
    git_init(proj)
    index_in_process(proj)
    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _endpoint_findings(cli_runner, proj, monkeypatch):
    monkeypatch.chdir(proj)
    result = invoke_cli(cli_runner, ["over-fetch"], cwd=proj, json_mode=True)
    data = parse_json_output(result, "over-fetch")
    assert_json_envelope(data, "over-fetch")
    return data


class TestThreeStateClassification:
    def test_bare_model_classified_high(self, cli_runner, bare_project, monkeypatch):
        data = _endpoint_findings(cli_runner, bare_project, monkeypatch)
        eps = data.get("endpoint_findings", [])
        bares = [e for e in eps if e["state"] == "BARE"]
        assert len(bares) == 1, f"Expected 1 BARE finding, got {eps}"
        assert bares[0]["severity"] == "H"
        assert "BareController" in bares[0]["endpoint"]

    def test_guarded_relation_classified_low(self, cli_runner, guarded_project, monkeypatch):
        data = _endpoint_findings(cli_runner, guarded_project, monkeypatch)
        eps = data.get("endpoint_findings", [])
        guarded = [e for e in eps if e["state"] == "GUARDED_RELATION"]
        assert len(guarded) == 1, f"Expected 1 GUARDED_RELATION, got {eps}"
        assert guarded[0]["severity"] == "L"
        # Evidence shows the partial column selection
        assert "manager:id,name" in guarded[0]["evidence"]

    def test_unguarded_relation_classified_high(self, cli_runner, unguarded_project, monkeypatch):
        data = _endpoint_findings(cli_runner, unguarded_project, monkeypatch)
        eps = data.get("endpoint_findings", [])
        unguarded = [e for e in eps if e["state"] == "UNGUARDED_RELATION"]
        assert len(unguarded) == 1, f"Expected 1 UNGUARDED_RELATION, got {eps}"
        assert unguarded[0]["severity"] == "H"
        assert "manager" in unguarded[0]["evidence"]

    def test_multiple_with_clauses_classified_individually(self, cli_runner, mixed_with_project, monkeypatch):
        """`with('a:cols', 'b')` should record both: 1 guarded + 1 unguarded relation."""
        data = _endpoint_findings(cli_runner, mixed_with_project, monkeypatch)
        eps = data.get("endpoint_findings", [])
        assert eps, f"Expected at least one endpoint finding, got {eps}"
        # The endpoint resolves to UNGUARDED_RELATION overall (worst wins),
        # but details.guarded should still list the partial-fix relation.
        target = next(e for e in eps if "MixedController" in e["endpoint"])
        assert target["state"] == "UNGUARDED_RELATION"
        guarded_rels = [g["relation"] for g in target["details"]["guarded"]]
        unguarded_rels = [u["relation"] for u in target["details"]["unguarded"]]
        assert "manager" in guarded_rels
        assert "department" in unguarded_rels

    def test_api_resource_wrapped_not_flagged(self, cli_runner, api_resource_project, monkeypatch):
        """EmployeeResource::collection(...) shapes output → no endpoint finding."""
        data = _endpoint_findings(cli_runner, api_resource_project, monkeypatch)
        eps = data.get("endpoint_findings", [])
        # The ResourceController body has a *Resource::collection( pattern,
        # which is in _BODY_SHAPING_PATTERNS → the body is treated as filtered.
        from_resource_ctrl = [e for e in eps if "ResourceController" in e["endpoint"]]
        assert from_resource_ctrl == [], f"Resource-wrapped endpoint should NOT be flagged, got {from_resource_ctrl}"

    def test_envelope_has_three_state_counts(self, cli_runner, workload_like_project, monkeypatch):
        data = _endpoint_findings(cli_runner, workload_like_project, monkeypatch)
        summary = data["summary"]
        # All three count fields are required by the spec
        assert "bare_count" in summary
        assert "guarded_relation_count" in summary
        assert "unguarded_relation_count" in summary
        # Union-like fixture has 1/3/1
        assert summary["bare_count"] == 1
        assert summary["guarded_relation_count"] == 3
        assert summary["unguarded_relation_count"] == 1
        # Real-leak count tracks BARE + UNGUARDED
        assert summary["real_leak_count"] == 2

    def test_verdict_mentions_three_states_distinctly(self, cli_runner, workload_like_project, monkeypatch):
        data = _endpoint_findings(cli_runner, workload_like_project, monkeypatch)
        verdict = data["summary"]["verdict"]
        # The verdict must distinguish all three buckets (LAW 4: concrete nouns)
        assert "BARE" in verdict, f"verdict missing BARE: {verdict!r}"
        assert "GUARDED_RELATION" in verdict, f"verdict missing GUARDED_RELATION: {verdict!r}"
        assert "UNGUARDED_RELATION" in verdict, f"verdict missing UNGUARDED_RELATION: {verdict!r}"

    def test_partial_success_flag_when_real_leak(self, cli_runner, workload_like_project, monkeypatch):
        """summary.partial_success must be true when ANY real leak (BARE or UNGUARDED) is found."""
        data = _endpoint_findings(cli_runner, workload_like_project, monkeypatch)
        summary = data["summary"]
        assert summary["partial_success"] is True
        assert summary["state"] == "leak"


# ---------------------------------------------------------------------------
# --leaks-only flag tests (W18.5 follow-up — CI fail-on-real-leaks-only mode)
# ---------------------------------------------------------------------------


def _endpoint_findings_with_args(cli_runner, proj, monkeypatch, extra_args):
    """Variant of _endpoint_findings that allows extra CLI args (e.g. --leaks-only)."""
    monkeypatch.chdir(proj)
    result = invoke_cli(cli_runner, ["over-fetch", *extra_args], cwd=proj, json_mode=True)
    data = parse_json_output(result, "over-fetch")
    assert_json_envelope(data, "over-fetch")
    return data


class TestLeaksOnlyFlag:
    """`--leaks-only` filters GUARDED_RELATION (advisory) findings out of the list.

    The flag is purely a presentation filter — summary counts always
    reflect the full classification. This is the W18.5 deferred follow-up:
    CI agents need a mode that fails only on real leaks (BARE/UNGUARDED),
    not on already-partially-guarded advisory findings.
    """

    def test_leaks_only_filters_guarded_relation(self, cli_runner, workload_like_project, monkeypatch):
        """Union-like fixture has 1 BARE + 1 UNGUARDED + 3 GUARDED.

        With --leaks-only, the findings list should contain only the 2 real
        leaks; the 3 GUARDED_RELATION entries are suppressed.
        """
        data = _endpoint_findings_with_args(cli_runner, workload_like_project, monkeypatch, ["--leaks-only"])
        eps = data.get("endpoint_findings", [])
        # 1 BARE + 1 UNGUARDED = 2 entries; zero GUARDED_RELATION in list
        assert len(eps) == 2, f"Expected 2 entries with --leaks-only, got {len(eps)}: {eps}"
        states = sorted(e["state"] for e in eps)
        assert states == ["BARE", "UNGUARDED_RELATION"], f"Expected exactly [BARE, UNGUARDED_RELATION], got {states}"
        # And no GUARDED_RELATION entries leaked through
        guarded = [e for e in eps if e["state"] == "GUARDED_RELATION"]
        assert guarded == [], f"GUARDED_RELATION must be filtered, found: {guarded}"

    def test_leaks_only_preserves_summary_counts(self, cli_runner, workload_like_project, monkeypatch):
        """summary tells the truth: all 3 counts present regardless of --leaks-only.

        The flag filters the FINDINGS list, not the SUMMARY. CI consumers
        can still see the suppressed count in summary.guarded_relation_count.
        """
        data = _endpoint_findings_with_args(cli_runner, workload_like_project, monkeypatch, ["--leaks-only"])
        summary = data["summary"]
        assert summary["bare_count"] == 1
        assert summary["guarded_relation_count"] == 3, (
            "guarded_relation_count must survive in summary even when filtered from list"
        )
        assert summary["unguarded_relation_count"] == 1
        assert summary["real_leak_count"] == 2
        # The endpoint_total reflects the full classification too
        assert summary["endpoint_total"] == 5
        # And the flag is recorded so consumers know the list was filtered
        assert summary["leaks_only"] is True

    def test_leaks_only_verdict_explains_suppression(self, cli_runner, workload_like_project, monkeypatch):
        """Verdict must name the suppressed count so the absence is explicit (Pattern 2)."""
        data = _endpoint_findings_with_args(cli_runner, workload_like_project, monkeypatch, ["--leaks-only"])
        verdict = data["summary"]["verdict"]
        # The suppression note must be visible in the one-line verdict
        assert "suppressed" in verdict.lower(), f"verdict must mention suppression, got: {verdict!r}"
        assert "--leaks-only" in verdict, f"verdict must name the flag that caused suppression, got: {verdict!r}"
        # The suppressed count (3) must appear
        assert "3" in verdict, f"verdict must mention the 3 suppressed findings: {verdict!r}"
        # And the real-leak counts are still surfaced
        assert "BARE" in verdict
        assert "UNGUARDED_RELATION" in verdict

    def test_leaks_only_default_off_preserves_existing_behavior(self, cli_runner, workload_like_project, monkeypatch):
        """Without --leaks-only, ALL three states surface in endpoint_findings (backward compat)."""
        data = _endpoint_findings_with_args(cli_runner, workload_like_project, monkeypatch, [])
        eps = data.get("endpoint_findings", [])
        # 1 + 3 + 1 = 5 entries when the flag is off
        assert len(eps) == 5, f"Expected 5 entries without flag, got {len(eps)}: {eps}"
        # And the leaks_only summary key reflects the off state
        assert data["summary"]["leaks_only"] is False
