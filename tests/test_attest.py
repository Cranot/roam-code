"""Tests for the roam attest command (D2: proof-carrying PRs)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_json_envelope,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enforcement_safe(monkeypatch):
    """Pre-elect autonomous_pr so privileged `roam attest` works under future
    `ROAM_MODE_ENFORCEMENT` default-on (W23.3 staged-rollout PR-B). All tests
    in this file invoke `roam attest`, which is gated under `safe_edit`."""
    monkeypatch.setenv("ROAM_AGENT_MODE", "autonomous_pr")


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def attest_project(tmp_path, monkeypatch):
    """Project with multiple files, indexed, with uncommitted changes.

    Creates a baseline, indexes, then modifies files to create a diff.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Source files
    (proj / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def display_name(self):\n"
        "        return self.name.title()\n"
    )

    (proj / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name, email):\n"
        "    user = User(name, email)\n"
        "    return user\n"
        "\n"
        "def get_display(user):\n"
        "    return user.display_name()\n"
    )

    (proj / "utils.py").write_text('def format_name(first, last):\n    return f"{first} {last}"\n')

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    # Modify files to create diff
    (proj / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name, email):\n"
        "    user = User(name, email)\n"
        "    if not email:\n"
        '        raise ValueError("email required")\n'
        "    return user\n"
        "\n"
        "def get_display(user):\n"
        "    return user.display_name()\n"
        "\n"
        "def new_helper():\n"
        "    return 42\n"
    )

    return proj


@pytest.fixture
def attest_no_changes(tmp_path, monkeypatch):
    """Project with no uncommitted changes."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "app.py").write_text('def main():\n    return "hello"\n')

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, f"index failed: {out}"

    return proj


# ---------------------------------------------------------------------------
# Basic command tests
# ---------------------------------------------------------------------------


class TestAttestCommand:
    """Test the attest CLI command."""

    def test_attest_runs(self, cli_runner, attest_project, monkeypatch):
        """Command exits 0."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project)
        assert result.exit_code == 0

    def test_attest_json_envelope(self, cli_runner, attest_project, monkeypatch):
        """Valid JSON envelope."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        assert_json_envelope(data, "attest")

    def test_attest_verdict_line(self, cli_runner, attest_project, monkeypatch):
        """Text output starts with VERDICT:."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project)
        assert result.output.strip().startswith("VERDICT:")

    def test_attest_no_changes(self, cli_runner, attest_no_changes, monkeypatch):
        """No changes -> graceful message."""
        monkeypatch.chdir(attest_no_changes)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_no_changes)
        assert result.exit_code == 0
        assert "no changes" in result.output.lower() or "No changes" in result.output

    def test_attest_no_changes_json(self, cli_runner, attest_no_changes, monkeypatch):
        """No changes in JSON mode -> valid envelope."""
        monkeypatch.chdir(attest_no_changes)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_no_changes, json_mode=True)
        data = parse_json_output(result, "attest")
        assert_json_envelope(data, "attest")
        assert "no changes" in data["summary"]["verdict"]


# ---------------------------------------------------------------------------
# Evidence section tests
# ---------------------------------------------------------------------------


class TestAttestEvidence:
    """Test that evidence sections are populated."""

    def test_attest_has_blast_radius(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains blast_radius evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        br = evidence.get("blast_radius", {})
        assert "changed_files" in br
        assert br["changed_files"] >= 1

    def test_attest_has_risk(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains risk evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        risk = evidence.get("risk")
        assert risk is not None
        assert "score" in risk
        assert "level" in risk

    def test_attest_has_breaking_changes(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains breaking_changes evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        bc = evidence.get("breaking_changes", {})
        assert "removed" in bc
        assert "signature_changed" in bc

    def test_attest_has_budget(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains budget evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        budget = evidence.get("budget", {})
        assert "rules_checked" in budget

    def test_attest_has_tests(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains tests evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        tests = evidence.get("tests", {})
        assert "selected" in tests
        assert "direct" in tests
        assert "transitive" in tests

    def test_attest_has_effects(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains effects evidence."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        evidence = data.get("evidence", {})
        effects = evidence.get("effects")
        assert isinstance(effects, list)

    def test_attest_has_attestation_metadata(self, cli_runner, attest_project, monkeypatch):
        """JSON output contains attestation metadata."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        att = data.get("attestation", {})
        assert att.get("version") == "1.0"
        assert att.get("tool") == "roam-code"
        assert "timestamp" in att
        assert "git_range" in att


# ---------------------------------------------------------------------------
# Format tests
# ---------------------------------------------------------------------------


class TestAttestFormats:
    """Test different output formats."""

    def test_attest_markdown_format(self, cli_runner, attest_project, monkeypatch):
        """--format markdown produces markdown output."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest", "--format", "markdown"], cwd=attest_project)
        assert result.exit_code == 0
        assert "## Roam Attestation" in result.output
        assert "Blast Radius" in result.output

    def test_attest_json_format(self, cli_runner, attest_project, monkeypatch):
        """--format json produces JSON output."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest", "--format", "json"], cwd=attest_project)
        data = json.loads(result.output)
        assert data["command"] == "attest"

    def test_attest_output_file(self, cli_runner, attest_project, monkeypatch):
        """--output writes to file."""
        monkeypatch.chdir(attest_project)
        out_path = attest_project / "attestation.txt"
        result = invoke_cli(cli_runner, ["attest", "--output", str(out_path)], cwd=attest_project)
        assert result.exit_code == 0
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "VERDICT:" in content


# ---------------------------------------------------------------------------
# Sign/hash tests
# ---------------------------------------------------------------------------


class TestAttestSign:
    """Test the --sign content hash feature."""

    def test_attest_sign_includes_hash(self, cli_runner, attest_project, monkeypatch):
        """--sign adds content_hash to attestation."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest", "--sign"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        att = data.get("attestation", {})
        assert "content_hash" in att
        assert att["content_hash"].startswith("sha256:")

    def test_attest_sign_hash_consistent(self, cli_runner, attest_project, monkeypatch):
        """Same evidence should produce the same hash."""
        monkeypatch.chdir(attest_project)
        result1 = invoke_cli(cli_runner, ["attest", "--sign"], cwd=attest_project, json_mode=True)
        result2 = invoke_cli(cli_runner, ["attest", "--sign"], cwd=attest_project, json_mode=True)
        data1 = parse_json_output(result1, "attest")
        data2 = parse_json_output(result2, "attest")
        # Hashes should be the same since evidence is deterministic
        hash1 = data1["attestation"]["content_hash"]
        hash2 = data2["attestation"]["content_hash"]
        assert hash1 == hash2

    def test_attest_no_sign_no_hash(self, cli_runner, attest_project, monkeypatch):
        """Without --sign, no content_hash in attestation."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        att = data.get("attestation", {})
        assert "content_hash" not in att


# ---------------------------------------------------------------------------
# Token-budget survival
# ---------------------------------------------------------------------------


class TestAttestSurvivesTokenBudget:
    """The token budget must never delete the attestation block.

    v13.7.0-v13.9.0: every JSON envelope over ~20000 tokens went through
    ``budget_truncate_json``, which preserved a fixed envelope set that did
    NOT include ``attestation`` and dropped the rest in dict insertion
    order. ``attestation`` is the first payload kwarg on this command, so it
    was deleted ahead of the ``evidence`` blob that was 98% of the payload.
    On a real repo ``roam attest --sign --format json`` therefore emitted a
    packet with no ``content_hash``, exit 0, and no warning -- the tamper
    seal silently gone.

    The rest of this file never caught it because ``attest_project`` is a
    three-file repo whose envelope is ~1000 tokens, far under the default
    cap. These tests force the cap down instead, via the same env lever
    production reads (``formatter._default_json_budget``), so the truncator
    genuinely fires on the toy fixture.
    """

    @pytest.fixture(autouse=True)
    def _tiny_json_budget(self, monkeypatch):
        monkeypatch.setenv("ROAM_DEFAULT_JSON_BUDGET", "200")

    def _truncated_envelope(self, cli_runner, project, *args):
        result = invoke_cli(cli_runner, ["attest", *args], cwd=project, json_mode=True)
        data = parse_json_output(result, "attest")
        # Guard the guard: if the budget did not fire, the test proves nothing.
        assert data["summary"].get("truncated") is True, "budget gate did not fire — fixture outgrew the cap?"
        return data

    def test_stale_if_survives_truncation(self, cli_runner, attest_project, monkeypatch):
        monkeypatch.chdir(attest_project)
        data = self._truncated_envelope(cli_runner, attest_project)
        att = data.get("attestation")
        assert att is not None, "token budget deleted the attestation block"
        conds = {c["condition"] for c in att["stale_if"]}
        assert TestAttestStalenessContract.CANONICAL <= conds, f"missing conditions: {conds}"

    def test_sign_content_hash_survives_truncation(self, cli_runner, attest_project, monkeypatch):
        """The --sign tamper seal must outlive truncation: a packet with no
        content_hash and no error is worse than no packet at all."""
        monkeypatch.chdir(attest_project)
        data = self._truncated_envelope(cli_runner, attest_project, "--sign")
        att = data.get("attestation")
        assert att is not None, "token budget deleted the attestation block"
        assert att.get("content_hash", "").startswith("sha256:"), "tamper seal lost to truncation"


# ---------------------------------------------------------------------------
# Verdict tests
# ---------------------------------------------------------------------------


@pytest.fixture
def attest_range(project_factory, tmp_path, monkeypatch):
    """Build and index both endpoints of a committed change."""
    from roam.commands import metrics_history

    def git(project, *args):
        return subprocess.check_output(["git", *args], cwd=project, text=True).strip()

    def commit(project, message):
        git(project, "add", ".")
        git(project, "commit", "-m", message)

    def index(project, timestamp):
        with monkeypatch.context() as clock:
            clock.setattr(metrics_history, "time", SimpleNamespace(time=lambda: timestamp))
            output, status = index_in_process(project)
        assert status == 0, output

    def build(change, *, base_snapshot=True, limit=100, submodule=False):
        api = "def total(price):\n    return price\n"
        supporting_code = "\n".join(f"def helper_{i}():\n    return {i}\n" for i in range(28))
        if change == "high-risk":
            supporting_code = "from api import total\n\n" + "\n".join(
                f"def caller_{i}():\n    return total({i})\n" for i in range(28)
            )
        files = {"api.py": api, "helpers.py": supporting_code}
        if change != "high-risk":
            files["test_api.py"] = "from api import total\n\ndef test_total():\n    assert total(10) == 10\n"
        project = project_factory(files, index=False)
        monkeypatch.chdir(project)
        monkeypatch.setenv("ROAM_DB_DIR", str(tmp_path / "db"))

        if submodule:
            module = project_factory({"lib.py": "def value():\n    return 1\n"}, index=False)
            git(project, "-c", "protocol.file.allow=always", "submodule", "add", str(module), "vendor")
            commit(project, "add the submodule")
        if base_snapshot:
            index(project, 1)

        replacement = {
            "body-only": api.replace("return price", "return price + 1"),
            "high-risk": api.replace("return price", "return price + 1"),
            "signature_changed": api.replace("total(price)", "total(price, tax)"),
            "renamed": api.replace("total", "subtotal"),
            "budget": api + "\ndef fee():\n    return 1\n\ndef tax():\n    return 2\n",
        }
        if change == "removed":
            (project / "api.py").unlink()
        elif change in replacement:
            (project / "api.py").write_text(replacement[change], encoding="utf-8")
        if submodule:
            (module / "lib.py").write_text("def value():\n    return 2\n", encoding="utf-8")
            commit(module, "advance lib")
            git(project, "-c", "protocol.file.allow=always", "submodule", "update", "--remote", "vendor")
        commit(project, "apply the change")
        index(project, 2)

        config = project / ".roam" / "budget.yaml"
        config.parent.mkdir(exist_ok=True)
        config.write_text(
            f"budgets:\n  - name: Symbol growth\n    metric: symbols\n    max_increase: {limit}\n",
            encoding="utf-8",
        )
        return project

    return build


def _range_attestation(cli_runner, project):
    args = ["attest", "HEAD~1..HEAD"]
    data = parse_json_output(invoke_cli(cli_runner, args, cwd=project, json_mode=True), "attest")
    plain = invoke_cli(cli_runner, args, cwd=project)
    markdown = invoke_cli(cli_runner, [*args, "--format", "markdown"], cwd=project)
    assert plain.exit_code == markdown.exit_code == 0
    return data, plain.output, markdown.output


def _assert_clearance(data, plain, markdown, expected):
    label, badge = {
        True: ("SAFE TO MERGE", "[PASS]"),
        False: ("NOT SAFE TO MERGE", "[FAIL]"),
        None: ("MERGE CLEARANCE UNAVAILABLE", "[UNKNOWN]"),
    }[expected]
    assert data["summary"]["safe_to_merge"] is expected
    assert data["verdict"]["safe_to_merge"] is expected
    assert data["summary"]["verdict"].lower().startswith(label.lower())
    assert plain.splitlines()[0] == f"VERDICT: {label}"
    assert f"**Verdict: {badge}**" in markdown


class TestAttestRangeClearance:
    @pytest.mark.parametrize(
        "base_snapshot,limit,status,safe",
        [(True, 0, "FAIL", False), (True, 2, "PASS", True), (False, 0, "SKIP", None)],
        ids=["exceeded", "within-limit", "missing-base"],
    )
    def test_budget_uses_range_base(self, cli_runner, attest_range, base_snapshot, limit, status, safe):
        project = attest_range("budget", base_snapshot=base_snapshot, limit=limit)
        data, plain, markdown = _range_attestation(cli_runner, project)
        budget = data["evidence"]["budget"]
        assert budget["rules_checked"] == 1
        assert budget["rules"][0]["status"] == status
        assert (budget["passed"], budget["failed"], budget["skipped"]) == (
            int(status == "PASS"),
            int(status == "FAIL"),
            int(status == "SKIP"),
        )
        if base_snapshot:
            assert budget["rules"][0]["delta"] == 2
        else:
            assert data["summary"]["partial_success"] is True
        assert ("1 budget(s) exceeded" in data["verdict"]["conditions"]) is (safe is False)
        assert data["summary"]["risk_level"] in {"LOW", "MODERATE"}
        assert f"[{status}] Symbol growth" in plain
        assert f"[{status}] Symbol growth" in markdown
        _assert_clearance(data, plain, markdown, safe)

    @pytest.mark.parametrize("change", ["body-only", "removed", "signature_changed", "renamed", "high-risk"])
    def test_real_evidence_controls_verdict(self, cli_runner, attest_range, change):
        project = attest_range(change)
        data, plain, markdown = _range_attestation(cli_runner, project)
        if change == "high-risk":
            assert data["summary"]["risk_level"] == "HIGH"
            assert "review high-risk changes" in data["verdict"]["conditions"]
            assert data["summary"]["breaking_changes"] == 0
        elif change == "body-only":
            assert data["summary"]["breaking_changes"] == 0
            assert data["evidence"]["budget"]["passed"] == 1
        else:
            item = data["evidence"]["breaking_changes"][change][0]
            assert item["old_name" if change == "renamed" else "name"] == "total"
            assert data["summary"]["breaking_changes"] == 1
        _assert_clearance(data, plain, markdown, change == "body-only")

    @pytest.mark.parametrize(
        "change,safe", [("submodule-only", None), ("body-only", None), ("signature_changed", False)]
    )
    def test_real_submodule_skip(self, cli_runner, attest_range, change, safe):
        project = attest_range(change, submodule=True)
        data, plain, markdown = _range_attestation(cli_runner, project)
        assert data["summary"]["partial_success"] is True
        assert data["files_not_analyzed"] == [{"file": "vendor", "reason": "gitlink"}]
        if change == "signature_changed":
            assert data["summary"]["breaking_changes"] == 1
        assert "vendor" in plain and "vendor" in markdown
        _assert_clearance(data, plain, markdown, safe)

    @pytest.mark.parametrize("phase,collector", [("risk", "_collect_risk"), ("budget", "_collect_budget_evidence")])
    @pytest.mark.parametrize("change,safe", [("body-only", None), ("signature_changed", False)])
    def test_clearance_requires_collected_evidence(
        self, cli_runner, attest_range, monkeypatch, phase, collector, change, safe
    ):
        from roam.commands import cmd_attest

        project = attest_range(change)

        def unavailable(*args, **kwargs):
            raise RuntimeError("collector unavailable")

        monkeypatch.setattr(cmd_attest, collector, unavailable)
        data, plain, markdown = _range_attestation(cli_runner, project)
        assert data["summary"]["partial_success"] is True
        assert any(warning.startswith(f"attest_collect_{phase}_failed:") for warning in data["warnings_out"])
        if change == "signature_changed":
            assert data["summary"]["breaking_changes"] == 1
        _assert_clearance(data, plain, markdown, safe)

    def test_invalid_budget_config_withholds_clearance(self, cli_runner, attest_range):
        project = attest_range("body-only")
        (project / ".roam" / "budget.yaml").write_text("budgets: malformed\n", encoding="utf-8")
        data, plain, markdown = _range_attestation(cli_runner, project)
        _assert_clearance(data, plain, markdown, None)
        assert data["summary"]["partial_success"] is True

    @pytest.mark.parametrize("limit,safe", [(0, False), (2, None)])
    def test_unmeasured_budget_does_not_count_as_exceeded(self, cli_runner, attest_range, monkeypatch, limit, safe):
        import sqlite3

        from roam.graph import cycles

        project = attest_range("budget", limit=limit)
        with (project / ".roam" / "budget.yaml").open("a", encoding="utf-8") as config:
            config.write("  - name: Cycle count\n    metric: cycles\n    max_increase: 0\n")

        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("cycles unavailable")

        monkeypatch.setattr(cycles, "find_cycles", unavailable)
        data, plain, markdown = _range_attestation(cli_runner, project)
        budget = data["evidence"]["budget"]
        assert budget["rules"][1]["status"] == "SKIP"
        assert budget["failed"] == int(safe is False)
        assert budget["skipped"] == 1
        assert data["summary"]["partial_success"] is True
        _assert_clearance(data, plain, markdown, safe)


class TestAttestVerdict:
    """Test verdict computation."""

    @pytest.mark.parametrize("breaking", [True, False], ids=["required-parameter", "body-only"])
    def test_signature_change_clearance(self, cli_runner, tmp_path, monkeypatch, breaking):
        monkeypatch.setenv("ROAM_DB_DIR", str(tmp_path / "db"))
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        api = project / "api.py"
        api.write_text("def total(price):\n    return price\n", encoding="utf-8")
        (project / "caller.py").write_text(
            "from api import total\n\n\n"
            "def receipt():\n    return total(10)\n\n\n"
            "def format_price(price):\n    return f'${price}'\n\n\n"
            "def format_quantity(quantity):\n    return str(quantity)\n\n\n"
            "def format_sku(sku):\n    return f'SKU {sku}'\n\n\n"
            "def format_date(date):\n    return date.isoformat()\n",
            encoding="utf-8",
        )
        (project / "test_api.py").write_text(
            "from api import total\n\n\ndef test_total():\n    assert total(10) > 0\n",
            encoding="utf-8",
        )
        git_init(project)
        params = "price, tax" if breaking else "price"
        api.write_text(f"def total({params}):\n    return price + 1\n", encoding="utf-8")
        out, rc = index_in_process(project, "--force")
        assert rc == 0, out

        result = invoke_cli(cli_runner, ["attest"], cwd=project, json_mode=True)
        data = parse_json_output(result, "attest")
        assert len(data["evidence"]["breaking_changes"]["signature_changed"]) == int(breaking)
        assert data["summary"]["risk_level"] in {"LOW", "MODERATE"}, data["summary"]

        plain = invoke_cli(cli_runner, ["attest"], cwd=project)
        markdown = invoke_cli(cli_runner, ["attest", "--format", "markdown"], cwd=project)
        assert data["summary"]["safe_to_merge"] is (not breaking)
        assert data["verdict"]["safe_to_merge"] is (not breaking)
        assert plain.output.splitlines()[0] == ("VERDICT: NOT SAFE TO MERGE" if breaking else "VERDICT: SAFE TO MERGE")
        assert ("**Verdict: [FAIL]**" if breaking else "**Verdict: [PASS]**") in markdown.output

    def test_partial_breaking_comparison_withholds_clearance(self, cli_runner, attest_project, monkeypatch):
        from roam.commands import cmd_attest

        monkeypatch.setattr(cmd_attest, "_collect_breaking", lambda *args: {"partial_success": True})
        monkeypatch.setattr(cmd_attest, "_collect_risk", lambda *args: {"score": 10, "level": "LOW"})
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        assert data["summary"]["safe_to_merge"] is None
        assert data["verdict"]["safe_to_merge"] is None
        assert data["summary"]["partial_success"] is True
        plain = invoke_cli(cli_runner, ["attest"], cwd=attest_project)
        markdown = invoke_cli(cli_runner, ["attest", "--format", "markdown"], cwd=attest_project)
        assert plain.output.splitlines()[0] == "VERDICT: MERGE CLEARANCE UNAVAILABLE"
        assert "**Verdict: [UNKNOWN]**" in markdown.output

    @pytest.mark.parametrize("limit, safe", [(0, False), (100, True)])
    def test_single_budget_controls_verdict_in_all_formats(self, cli_runner, attest_project, monkeypatch, limit, safe):
        from roam.commands import cmd_attest
        from roam.graph import diff

        config = attest_project / ".roam" / "budget.yaml"
        config.parent.mkdir(exist_ok=True)
        config.write_text(f"budgets:\n  - name: Symbol growth\n    metric: symbols\n    max_increase: {limit}\n")
        monkeypatch.setattr(diff, "find_before_snapshot", lambda *args, **kwargs: {"symbols": 0})
        monkeypatch.setattr(cmd_attest, "_collect_breaking", lambda *args: {})
        monkeypatch.setattr(cmd_attest, "_collect_risk", lambda *args: {"score": 10, "level": "LOW"})

        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        budget = data["evidence"]["budget"]
        assert budget["rules_checked"] == 1
        assert budget["failed"] == int(not safe)
        assert budget["rules"][0]["delta"] > 0
        assert data["summary"]["safe_to_merge"] is safe
        assert data["verdict"]["safe_to_merge"] is safe
        assert ("1 budget(s) exceeded" in data["verdict"]["conditions"]) is (not safe)

        plain = invoke_cli(cli_runner, ["attest"], cwd=attest_project)
        markdown = invoke_cli(cli_runner, ["attest", "--format", "markdown"], cwd=attest_project)
        assert plain.output.splitlines()[0] == ("VERDICT: SAFE TO MERGE" if safe else "VERDICT: NOT SAFE TO MERGE")
        assert ("**Verdict: [PASS]**" if safe else "**Verdict: [FAIL]**") in markdown.output

    @pytest.mark.parametrize("blocker", ["breaking", "risk", "budget"])
    @pytest.mark.parametrize("partial", [False, True])
    def test_refusal_survives_partial_comparison(self, cli_runner, attest_project, monkeypatch, blocker, partial):
        from roam.commands import cmd_attest

        comparison = {"removed": [{"name": "old_api"}]} if blocker == "breaking" else {}
        if partial:
            comparison["files_not_analyzed"] = [{"file": "vendor/submodule"}]
        risk = {"score": 75, "level": "HIGH"} if blocker == "risk" else {"score": 10, "level": "LOW"}
        budget = {"rules_checked": 1, "passed": 0, "failed": 1, "skipped": 0, "rules": []}
        monkeypatch.setattr(cmd_attest, "_collect_breaking", lambda *args: comparison)
        monkeypatch.setattr(cmd_attest, "_collect_risk", lambda *args: risk)
        if blocker == "budget":
            monkeypatch.setattr(cmd_attest, "_collect_budget_evidence", lambda *args: budget)

        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        assert data["summary"].get("partial_success", False) is partial
        assert data["summary"]["safe_to_merge"] is False
        assert data["verdict"]["safe_to_merge"] is False
        assert data["summary"]["verdict"].startswith("NOT safe to merge")
        plain = invoke_cli(cli_runner, ["attest"], cwd=attest_project)
        markdown = invoke_cli(cli_runner, ["attest", "--format", "markdown"], cwd=attest_project)
        assert plain.output.splitlines()[0] == "VERDICT: NOT SAFE TO MERGE"
        assert "**Verdict: [FAIL]**" in markdown.output
        if partial:
            assert "submodule path" in data["summary"]["verdict"]
            assert "vendor/submodule" in plain.output
            assert "vendor/submodule" in markdown.output

    def test_verdict_safe_by_default(self, cli_runner, attest_project, monkeypatch):
        """Small changes should be safe to merge by default."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        verdict = data.get("verdict", {})
        assert isinstance(verdict.get("safe_to_merge"), bool)

    def test_verdict_has_conditions(self, cli_runner, attest_project, monkeypatch):
        """Verdict should include conditions list."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        verdict = data.get("verdict", {})
        assert "conditions" in verdict
        assert isinstance(verdict["conditions"], list)

    def test_verdict_in_summary(self, cli_runner, attest_project, monkeypatch):
        """Summary should include safe_to_merge and risk info."""
        monkeypatch.chdir(attest_project)
        result = invoke_cli(cli_runner, ["attest"], cwd=attest_project, json_mode=True)
        data = parse_json_output(result, "attest")
        summary = data.get("summary", {})
        assert "safe_to_merge" in summary
        assert "verdict" in summary


# ---------------------------------------------------------------------------
# Unit tests for internal helpers
# ---------------------------------------------------------------------------


class TestContentHash:
    """Test the content hash helper."""

    def test_content_hash_deterministic(self):
        from roam.commands.cmd_attest import _content_hash

        evidence = {"risk": {"score": 42}, "tests": {"selected": 5}}
        h1 = _content_hash(evidence)
        h2 = _content_hash(evidence)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_content_hash_changes_with_data(self):
        from roam.commands.cmd_attest import _content_hash

        e1 = {"risk": {"score": 42}}
        e2 = {"risk": {"score": 99}}
        assert _content_hash(e1) != _content_hash(e2)


class TestComputeVerdict:
    """Test the verdict computation."""

    def test_safe_when_no_issues(self):
        from roam.commands.cmd_attest import _compute_verdict

        risk = {"score": 10, "level": "LOW"}
        breaking = {"removed": [], "signature_changed": [], "renamed": []}
        fitness = {"rules": [], "violations": []}
        budget = {"passed": 3, "failed": 0, "skipped": 0, "rules": []}
        v = _compute_verdict(risk, breaking, fitness, budget)
        assert v["safe_to_merge"] is True

    def test_unsafe_when_budget_exceeded(self):
        from roam.commands.cmd_attest import _compute_verdict

        risk = {"score": 10, "level": "LOW"}
        breaking = {"removed": [], "signature_changed": [], "renamed": []}
        fitness = {"rules": [], "violations": []}
        budget = {"passed": 2, "failed": 1, "skipped": 0, "rules": []}
        v = _compute_verdict(risk, breaking, fitness, budget)
        assert v["safe_to_merge"] is False

    def test_warnings_on_breaking_changes(self):
        from roam.commands.cmd_attest import _compute_verdict

        risk = {"score": 10, "level": "LOW"}
        breaking = {"removed": [{"name": "foo"}], "signature_changed": [], "renamed": []}
        fitness = {"rules": [], "violations": []}
        budget = {"passed": 3, "failed": 0, "skipped": 0, "rules": []}
        v = _compute_verdict(risk, breaking, fitness, budget)
        assert any("breaking" in w for w in v["warnings"])

    def test_warnings_on_high_risk(self):
        from roam.commands.cmd_attest import _compute_verdict

        risk = {"score": 80, "level": "CRITICAL"}
        breaking = {"removed": [], "signature_changed": [], "renamed": []}
        fitness = {"rules": [], "violations": []}
        budget = {"passed": 3, "failed": 0, "skipped": 0, "rules": []}
        v = _compute_verdict(risk, breaking, fitness, budget)
        assert v["safe_to_merge"] is False
        assert any("CRITICAL" in w for w in v["warnings"])


class TestAttestStalenessContract:
    """Issue #58: the attestation must carry a first-class staleness contract
    so a downstream agent reading it later can decide whether it is still
    authoritative or the evidence must be re-run."""

    CANONICAL = {
        "head_commit_changed",
        "indexed_commit_behind_head",
        "working_tree_dirty",
        "rule_config_hash_changed",
        "roam_version_major_changed",
        "coverage_import_changed",
        "target_branch_moved",
    }

    def _attestation(self, cli_runner, project):
        result = invoke_cli(cli_runner, ["attest"], cwd=project, json_mode=True)
        return parse_json_output(result, "attest")["attestation"]

    def test_stale_if_contract_present(self, cli_runner, attest_project, monkeypatch):
        monkeypatch.chdir(attest_project)
        a = self._attestation(cli_runner, attest_project)
        assert isinstance(a.get("stale_if"), list) and a["stale_if"]
        conds = {c["condition"] for c in a["stale_if"]}
        assert self.CANONICAL <= conds, f"missing conditions: {self.CANONICAL - conds}"
        for c in a["stale_if"]:
            assert "condition" in c and "baseline" in c

    def test_commits_promoted_unconditionally(self, cli_runner, attest_project, monkeypatch):
        # Regression vs the old only-when-stale behaviour: both commits are in
        # the durable body even when the index is fresh at emit time.
        monkeypatch.chdir(attest_project)
        a = self._attestation(cli_runner, attest_project)
        assert "indexed_commit" in a and "head_commit" in a

    def test_verification_command_runnable(self, cli_runner, attest_project, monkeypatch):
        monkeypatch.chdir(attest_project)
        a = self._attestation(cli_runner, attest_project)
        assert a["verification_command"].startswith("roam attest ")
        assert "--format json" in a["verification_command"]

    def test_privacy_block_is_honest(self, cli_runner, attest_project, monkeypatch):
        monkeypatch.chdir(attest_project)
        a = self._attestation(cli_runner, attest_project)
        p = a["privacy"]
        assert p["source_code_included"] is False
        # honesty guard: symbol/identifier names ARE embedded, so we say so
        assert p["identifiers_included"] is True
        assert p["network_required"] is False
        assert p["telemetry"] == "none"

    def test_not_checked_is_a_list(self, cli_runner, attest_project, monkeypatch):
        monkeypatch.chdir(attest_project)
        a = self._attestation(cli_runner, attest_project)
        assert isinstance(a.get("not_checked"), list)
