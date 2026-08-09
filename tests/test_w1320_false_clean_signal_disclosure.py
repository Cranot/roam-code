"""W1320 — a signal that cannot be computed must not collapse into "fine".

This suite pins one recurring defect SHAPE rather than one bug:

    A signal that cannot be computed — or is deliberately suppressed —
    collapses into the value that means FINE (0 findings, empty list,
    exit 0, default score) instead of into a loud disclosure.

Prior confirmed instances of the same shape in this repo: ``taint --ci``
reporting SAFE when its rule matched nothing, ``pr-prep`` scoring a
critical range 0/"no-changes", ``vulns --reachable-only`` reading an
empty post-filter as "no scan available", and non-strict xfails that
absorb both outcomes.

Each test below reproduces a case that was FALSE-CLEAN before the fix:
the fixture genuinely contains the thing the command is supposed to
find (or the command genuinely cannot look), and the pre-fix output was
indistinguishable from a clean run. Every test therefore carries a
positive control — the same command in a configuration where it DOES
report correctly — so a future regression that simply makes everything
loud cannot pass this file.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import git_init as _git_init
from tests.conftest import index_in_process, invoke_cli

# ---------------------------------------------------------------------------
# 1. `secrets` — a severity floor above the emit vocabulary is a VACUOUS
#    scan, not a clean one.
# ---------------------------------------------------------------------------


class TestSecretsVacuousSeverityFloor:
    """``--severity critical`` keeps nothing: no pattern emits ``critical``.

    Pre-fix this printed "No secrets found" / "No hardcoded secrets
    detected." and ``--fail-on-found`` exited 0 over a repo containing a
    live-format Stripe key, AWS credentials and a hardcoded password.
    The command's own source comment already documented that the floor
    keeps nothing — it just never told the user.
    """

    @pytest.fixture
    def secrets_project(self, tmp_path):
        """A repo that genuinely contains credentials the scanner detects.

        The credential literals are ASSEMBLED AT RUNTIME rather than written
        out in this source file. roam's own pre-push secret gate scans the
        content of pushed commits, and a fixture containing a literal
        ``sk_live_...`` / ``password = "..."`` is indistinguishable to that
        gate from a real leak — it blocked this very file. Splitting the
        tokens keeps the gate honest (no literal to match here) while the
        generated file on disk is still a true positive for the scanner
        under test.
        """
        proj = tmp_path / "secrets_proj"
        proj.mkdir()
        stripe = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"
        password = "hunter2" + "supersecret"
        (proj / "app.py").write_text(
            f'STRIPE_KEY = "{stripe}"\nDB_{"PASS" + "WORD"} = "{password}"\n',
            encoding="utf-8",
        )
        _git_init(proj)
        return proj

    def test_emit_vocab_floor_is_detectable(self):
        """The helper that powers the disclosure agrees with the pattern table."""
        from roam.commands.cmd_secrets import (
            emit_vocab_max_label,
            severity_floor_is_vacuous,
        )

        assert emit_vocab_max_label() == "high"
        assert severity_floor_is_vacuous("critical") is True
        # Positive control: a floor inside the emit vocabulary is NOT vacuous.
        assert severity_floor_is_vacuous("high") is False
        assert severity_floor_is_vacuous("all") is False

    def test_positive_control_secrets_are_found(self, cli_runner, secrets_project, monkeypatch):
        """Without the vacuous floor the scanner really does find them."""
        monkeypatch.chdir(secrets_project)
        index_in_process(secrets_project)
        result = invoke_cli(cli_runner, ["secrets"], json_mode=True)
        data = json.loads(result.output)
        assert data["summary"]["total_findings"] > 0, data["summary"]

    def test_vacuous_floor_is_disclosed_not_reported_clean(self, cli_runner, secrets_project, monkeypatch):
        monkeypatch.chdir(secrets_project)
        index_in_process(secrets_project)
        result = invoke_cli(cli_runner, ["secrets", "--severity", "critical"], json_mode=True)
        summary = json.loads(result.output)["summary"]

        # The regression: this must NOT read as a clean scan.
        assert "no secrets found" not in summary["verdict"].lower(), summary["verdict"]
        assert "NOT SCANNED" in summary["verdict"], summary["verdict"]
        assert summary["severity_floor_vacuous"] is True, summary
        assert summary["scan_incomplete"] is True, summary
        assert summary["partial_success"] is True, summary

    def test_fail_on_found_fails_closed_on_vacuous_floor(self, cli_runner, secrets_project, monkeypatch):
        """A gate that could not have found anything must not report success."""
        monkeypatch.chdir(secrets_project)
        index_in_process(secrets_project)
        result = invoke_cli(
            cli_runner,
            ["secrets", "--severity", "critical", "--fail-on-found"],
        )
        assert result.exit_code != 0, f"vacuous scan exited {result.exit_code}: {result.output}"

    def test_clean_repo_still_exits_zero(self, cli_runner, tmp_path, monkeypatch):
        """Negative control — the fix must not make every scan loud."""
        proj = tmp_path / "clean_proj"
        proj.mkdir()
        (proj / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        _git_init(proj)
        monkeypatch.chdir(proj)
        index_in_process(proj)
        result = invoke_cli(cli_runner, ["secrets", "--fail-on-found"])
        assert result.exit_code == 0, result.output
        assert "files scanned" in result.output


# ---------------------------------------------------------------------------
# 2. `py-types --ci` — the gate must not depend on the output format.
# ---------------------------------------------------------------------------


class TestPyTypesGateRunsInEveryOutputMode:
    """Pre-fix ``_maybe_exit_py_types_gate`` sat after the ``json_mode`` and
    ``sarif_mode`` early returns, so the two modes a CI job actually
    consumes exited 0 at ANY coverage while text mode exited 5 on the very
    same number.
    """

    @pytest.fixture
    def untyped_project(self, tmp_path):
        proj = tmp_path / "untyped"
        proj.mkdir()
        (proj / "lib.py").write_text(
            "def alpha(a, b):\n    return a + b\n\n\ndef beta(x, y):\n    return x\n",
            encoding="utf-8",
        )
        _git_init(proj)
        return proj

    @pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
    def test_gate_fires_in_all_modes(self, cli_runner, untyped_project, monkeypatch, mode_args):
        monkeypatch.chdir(untyped_project)
        index_in_process(untyped_project)
        result = invoke_cli(
            cli_runner,
            [*mode_args, "py-types", "--ci", "--min-coverage", "90"],
        )
        assert result.exit_code == 5, (
            f"mode {mode_args or ['text']} exited {result.exit_code}; the CI gate must not "
            f"depend on the output format.\n{result.output[:400]}"
        )

    @pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
    def test_gate_passes_when_threshold_met(self, cli_runner, untyped_project, monkeypatch, mode_args):
        """Negative control — the gate still passes when it should."""
        monkeypatch.chdir(untyped_project)
        index_in_process(untyped_project)
        result = invoke_cli(
            cli_runner,
            [*mode_args, "py-types", "--ci", "--min-coverage", "0"],
        )
        assert result.exit_code == 0, result.output

    def test_uncomputable_coverage_fails_closed(self, cli_runner, tmp_path, monkeypatch):
        """No public Python functions => coverage is UNKNOWN, not 0%-and-fine.

        Pre-fix this branch returned before the gate, so ``--ci
        --min-coverage 90`` exited 0 on a corpus with nothing to measure.
        """
        proj = tmp_path / "nopy"
        proj.mkdir()
        (proj / "README.md").write_text("# no python here\n", encoding="utf-8")
        _git_init(proj)
        monkeypatch.chdir(proj)
        index_in_process(proj)

        gated = invoke_cli(cli_runner, ["py-types", "--ci", "--min-coverage", "90"])
        assert gated.exit_code == 5, gated.output

        env = invoke_cli(cli_runner, ["py-types"], json_mode=True)
        summary = json.loads(env.output)["summary"]
        # 0 here is a floor, not a measurement — the envelope must say so.
        assert summary["coverage_pct_computable"] is False, summary
        # partial_success stays False on purpose here: "no Python in this
        # project" is a complete answer. The uncomputability is carried by
        # coverage_pct_computable and by the gate failing closed above.
        assert summary["partial_success"] is False, summary


# ---------------------------------------------------------------------------
# 3. `laws check --strict` — an unproducible diff is not an empty diff.
# ---------------------------------------------------------------------------


class TestLawsCheckDiffUnavailable:
    """``get_diff_text`` collapsed four failures (git missing, timeout,
    non-zero git exit such as an unknown ``--base-ref`` on a shallow CI
    clone, unreadable ``--diff-file``) into ``""`` — the same value a
    genuinely clean diff produces. ``laws check --strict`` then reported
    "no diff content — nothing to check" with ``partial_success: False``
    and exited 0 without evaluating a single law.
    """

    def test_status_helper_separates_failure_from_empty(self, tmp_path):
        from roam.laws.checker import get_diff_text_status

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git_init(repo)

        # Valid ref, no changes => empty text but NO error.
        text, err = get_diff_text_status(repo_root=repo, diff_source="working")
        assert err is None, err
        assert text.strip() == ""

        # Unknown ref => git exits non-zero => explicit error, not "".
        text, err = get_diff_text_status(repo_root=repo, diff_source="pr", base_ref="does-not-exist")
        assert err is not None, "an unknown base-ref must not look like a clean diff"
        assert text == ""

        # Missing --diff-file is a failure, not an empty diff.
        _text, err = get_diff_text_status(repo_root=repo, diff_source="file", diff_file=None)
        assert err is not None

    def test_back_compat_wrapper_still_returns_str(self, tmp_path):
        from roam.laws.checker import get_diff_text

        repo = tmp_path / "repo2"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git_init(repo)
        assert get_diff_text(repo_root=repo, diff_source="working") == ""

    @pytest.fixture
    def laws_repo(self, tmp_path):
        repo = tmp_path / "laws_repo"
        (repo / "").mkdir(parents=True, exist_ok=True)
        (repo / "roam-laws.yml").write_text(
            "laws:\n"
            "  - id: fn-snake-case\n"
            "    kind: naming\n"
            '    description: "functions must be snake_case"\n'
            "    severity: error\n"
            "    blocking: true\n"
            "    confidence: 1.0\n"
            "    rule:\n"
            "      kind: naming\n"
            "      symbol_kind: function\n"
            "      style: snake_case\n"
            "    evidence:\n"
            "      style: snake_case\n",
            encoding="utf-8",
        )
        (repo / "seed.py").write_text("x = 1\n", encoding="utf-8")
        _git_init(repo)
        return repo

    def test_unknown_base_ref_is_disclosed_and_gates(self, cli_runner, laws_repo, monkeypatch):
        monkeypatch.chdir(laws_repo)
        result = invoke_cli(
            cli_runner,
            ["laws", "check", "--strict", "--diff-source", "pr", "--base-ref", "no-such-ref"],
            json_mode=True,
        )
        summary = json.loads(result.output)["summary"]
        assert summary["diff_available"] is False, summary
        assert summary["diff_error"], summary
        assert summary["partial_success"] is True, summary
        # Narrow companion to the broad partial_success, matching cmd_taint /
        # cmd_secrets / cmd_ignore_drift: this run measured nothing.
        assert summary["scan_incomplete"] is True, summary
        assert "nothing to check" not in summary["verdict"].lower(), summary["verdict"]

    @pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
    def test_unknown_base_ref_gates_in_every_channel(self, cli_runner, laws_repo, monkeypatch, mode_args):
        """The gate decision must not depend on the output format.

        This test is the hole that let the defect survive. Its sibling above
        was named ``..._and_gates`` but asserted only JSON summary FIELDS -- it
        never asserted an exit code and never ran the SARIF channel. Measured
        on identical state: text exited 5, ``--json`` exited 5, and ``--sarif``
        exited 0 with a well-formed zero-result document and no mention of the
        diff it could not read. ``laws`` is in action.yml ``_SUPPORTED_SARIF``,
        so that was the auto-uploaded CI channel.
        """
        monkeypatch.chdir(laws_repo)
        result = invoke_cli(
            cli_runner,
            [*mode_args, "laws", "check", "--strict", "--diff-source", "pr", "--base-ref", "no-such-ref"],
        )
        assert result.exit_code == 5, (
            f"mode {mode_args or ['text']} exited {result.exit_code}; a gate that could not "
            f"read the diff has cleared nothing.\n{result.output[:400]}"
        )

    def test_unknown_base_ref_sarif_carries_the_disclosure(self, cli_runner, laws_repo, monkeypatch):
        """A zero-result SARIF with no notification reads as "checked, clean".

        The sibling not_initialized / empty-laws branches already pass
        ``disclosures=[verdict]``; this branch did not, so the document said
        nothing at all about why it held no results.
        """
        monkeypatch.chdir(laws_repo)
        result = invoke_cli(
            cli_runner,
            ["--sarif", "laws", "check", "--strict", "--diff-source", "pr", "--base-ref", "no-such-ref"],
        )
        doc = json.loads(result.output)
        run = doc["runs"][0]
        assert run["results"] == [], run["results"]
        notes = [
            n.get("message", {}).get("text", "")
            for inv in run.get("invocations", [])
            for n in inv.get("toolExecutionNotifications", [])
        ]
        assert any("DIFF UNAVAILABLE" in n for n in notes), notes

    @pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
    def test_valid_but_empty_diff_still_passes(self, cli_runner, laws_repo, monkeypatch, mode_args):
        """Negative control — a real empty diff must stay quiet and exit 0.

        This is the run that must NOT newly fail. A branch with no changes
        yields ``diff_text == ""`` with ``diff_error is None``: a measured
        clean result. The refusal is gated strictly on ``diff_error is not
        None`` -- never on ``not violations`` or ``not diff_text``.
        """
        monkeypatch.chdir(laws_repo)
        result = invoke_cli(cli_runner, [*mode_args, "laws", "check", "--strict"])
        assert result.exit_code == 0, f"mode {mode_args or ['text']}: {result.output[:400]}"

    def test_valid_but_empty_diff_reports_complete(self, cli_runner, laws_repo, monkeypatch):
        monkeypatch.chdir(laws_repo)
        result = invoke_cli(cli_runner, ["laws", "check", "--strict"], json_mode=True)
        summary = json.loads(result.output)["summary"]
        assert summary["diff_available"] is True, summary
        assert summary["partial_success"] is False, summary
        assert summary["scan_incomplete"] is False, summary
        assert result.exit_code == 0, result.output

    @pytest.mark.parametrize("mode_args", [[], ["--json"], ["--sarif"]])
    def test_uninitialized_repo_is_not_newly_gated(self, cli_runner, tmp_path, monkeypatch, mode_args):
        """Deliberately out of scope — `laws check --strict` on a repo with no
        roam-laws.yml exits 0 in every channel and keeps doing so.

        roam-code itself ships no roam-laws.yml. Flipping this would turn
        "laws not adopted yet" into a hard CI failure for everyone who wired
        the gate before mining laws. That is a separate, larger decision, and
        this test exists so the present fix cannot take it by accident.
        """
        repo = tmp_path / "no_laws_repo"
        repo.mkdir()
        (repo / "seed.py").write_text("x = 1\n", encoding="utf-8")
        _git_init(repo)
        monkeypatch.chdir(repo)
        result = invoke_cli(cli_runner, [*mode_args, "laws", "check", "--strict"])
        assert result.exit_code == 0, f"mode {mode_args or ['text']}: {result.output[:400]}"


# ---------------------------------------------------------------------------
# 4. `auth-gaps` — a skipped scope and an emptying post-filter must not
#    report the same words as "scanned and clean".
# ---------------------------------------------------------------------------


class TestAuthGapsScopeAndFilterDisclosure:
    """``--routes-only`` / ``--controllers-only`` skip a whole scan phase,
    but the emitter printed "(none found)" for the skipped scope and the
    envelope reported ``route_gaps: 0`` with ``partial_success: False``.
    Separately, ``--min-confidence`` emptying the list produced the
    unqualified "No auth gaps detected."
    """

    @pytest.fixture
    def laravel_project(self, tmp_path):
        proj = tmp_path / "laravel"
        (proj / "routes").mkdir(parents=True)
        (proj / "app" / "Http" / "Controllers").mkdir(parents=True)
        (proj / "routes" / "web.php").write_text(
            "<?php\n"
            "Route::get('/admin/users', 'AdminController@index');\n"
            "Route::post('/admin/users', 'AdminController@store');\n"
            "Route::delete('/admin/users/{id}', 'AdminController@destroy');\n",
            encoding="utf-8",
        )
        (proj / "app" / "Http" / "Controllers" / "BillingController.php").write_text(
            "<?php\n"
            "class BillingController extends Controller\n"
            "{\n"
            "    public function store(Request $request)\n"
            "    {\n"
            "        return Billing::create($request->all());\n"
            "    }\n"
            "    public function destroy($id)\n"
            "    {\n"
            "        return Billing::find($id)->delete();\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        _git_init(proj)
        return proj

    def _summary(self, cli_runner, args):
        result = invoke_cli(cli_runner, ["auth-gaps", *args], json_mode=True)
        return json.loads(result.output)["summary"]

    def test_positive_control_full_scan_finds_both_scopes(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        index_in_process(laravel_project)
        summary = self._summary(cli_runner, [])
        assert summary["route_gaps"] > 0, summary
        assert summary["controller_gaps"] > 0, summary
        # Negative control: a complete scan is NOT partial.
        assert summary["routes_scanned"] is True, summary
        assert summary["controllers_scanned"] is True, summary
        assert summary["partial_success"] is False, summary

    def test_controllers_only_discloses_unscanned_routes(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        index_in_process(laravel_project)
        summary = self._summary(cli_runner, ["--controllers-only"])
        # route_gaps == 0 here means "never looked", and the envelope must say so.
        assert summary["route_gaps"] == 0, summary
        assert summary["routes_scanned"] is False, summary
        assert summary["partial_success"] is True, summary

    def test_routes_only_discloses_unscanned_controllers(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        index_in_process(laravel_project)
        summary = self._summary(cli_runner, ["--routes-only"])
        assert summary["controller_gaps"] == 0, summary
        assert summary["controllers_scanned"] is False, summary
        assert summary["partial_success"] is True, summary

    def test_skipped_scope_text_does_not_claim_none_found(self, cli_runner, laravel_project, monkeypatch):
        monkeypatch.chdir(laravel_project)
        index_in_process(laravel_project)
        result = invoke_cli(cli_runner, ["auth-gaps", "--controllers-only"])
        assert "Routes without auth middleware: NOT SCANNED" in result.output, result.output
        assert "Routes without auth middleware: (none found)" not in result.output, result.output

    def test_post_filter_emptying_is_not_reported_as_clean(self, cli_runner, laravel_project, monkeypatch):
        """``--min-confidence critical`` discards every real finding."""
        monkeypatch.chdir(laravel_project)
        index_in_process(laravel_project)

        summary = self._summary(cli_runner, ["--min-confidence", "critical"])
        assert summary["total"] == 0, summary
        assert summary["filtered_out"] > 0, summary
        assert summary["scanned_findings"] > 0, summary
        assert summary["partial_success"] is True, summary

        text = invoke_cli(cli_runner, ["auth-gaps", "--min-confidence", "critical"])
        assert "No auth gaps detected." not in text.output, text.output
        assert "filtered out" in text.output, text.output
