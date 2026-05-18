"""W1005-followup-G -- short-code severity widened with canonical alias map.

Pattern 3a (cross-command metric divergence). Pre-W1005-followup-G, two
commands accepted ONLY the H/M/L short-code emit vocab:

* ``roam dogfood-aggregate --severity`` (cmd_dogfood_aggregate)
* ``roam pr-bundle add risk --severity`` (cmd_pr_bundle)

An agent fluent in the W547 canonical vocab (``critical / error / high /
warning / medium / low / info / note``) who typed ``--severity high``
(because that's what ``roam smells``, ``roam alerts``, ``roam api-changes``,
etc. accept post-W1005 / post-W1005-followup-F) hit a click usage error 2.

Path A (the chosen fix, mirroring W1005-followup-F). Widen Click.Choice
to accept BOTH vocabs; project canonical tokens onto H/M/L via
:data:`_CANONICAL_TO_SHORTCODE` (dogfood-aggregate) /
:data:`_CANONICAL_TO_RISK_SHORTCODE` (pr-bundle) BEFORE the filter site
(dogfood-aggregate) or BEFORE the on-disk store (pr-bundle). The EMIT
vocab stays H/M/L so downstream consumers (the ``by_severity`` /
``risk_severity_distribution`` bucket aggregators) are unchanged. The
INPUT vocab is the union.

Projection (one-way; same shape for both commands):
    critical / error / high -> H
    warning / medium        -> M
    info / low / note       -> L

Two test classes -- one per command -- pin:

1. Canonical-token parses (``--severity high`` no longer trips click
   usage error 2).
2. Short-code parses (back-compat unchanged).
3. Case insensitivity (``h`` and ``HIGH`` both work).
4. Floor semantic (``--severity M`` keeps H+M but drops L).
5. Emit vocab unchanged (output still surfaces ``H``/``M``/``L`` strings).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests._helpers.repo_root import repo_root

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, invoke_cli  # noqa: E402

# Resolve the canonical repo root so the test file lives correctly even
# when dispatched through a nested worktree (W572 lesson).
REPO_ROOT = repo_root()


# ===========================================================================
# Helpers
# ===========================================================================


def _parse_json(text: str) -> dict:
    """Parse the trailing JSON envelope from CLI stdout."""
    return json.loads(text.strip())


# ---------------------------------------------------------------------------
# dogfood-aggregate fixture -- a tiny corpus with one finding per severity
# tier so the floor predicate is observable.
# ---------------------------------------------------------------------------


def _write_eval(
    base: Path,
    command: str,
    slug: str,
    findings: list[tuple[str, str, str]],
    *,
    status: str = "open",
    date: str = "2026-05-17",
) -> Path:
    """Write one synthetic eval with the given findings table."""
    folder = base / command
    folder.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        f"command: {command}",
        f"date: {date}",
        "roam_version: 12.50",
        f"task: {slug}",
        "verdict: use-with-caveats",
        f"status: {status}",
    ]
    table_rows = [
        f"| {i + 1} | {sev} | {typ} | {obs} | suggestion {i + 1} |" for i, (sev, typ, obs) in enumerate(findings)
    ]
    lines = [
        "---",
        *fm_lines,
        "---",
        "",
        f"# Roam Eval - {command} - {slug}",
        "",
        "**Why:** synthetic test fixture.",
        "**TL;DR:** synthetic test fixture.",
        "",
        "| # | Sev | Type    | Observation | Suggestion |",
        "|---|-----|---------|-------------|------------|",
        *table_rows,
        "",
    ]
    body = "\n".join(lines)
    path = folder / f"{date}-{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def evals_dir_three_tiers(tmp_path):
    """Build a corpus with one open eval carrying one H, one M, one L row."""
    base = tmp_path / "evals"
    _write_eval(
        base,
        "complexity",
        "three-tier-fixture",
        findings=[
            ("H", "wrong", "blocker tier"),
            ("M", "signal", "mid tier"),
            ("L", "noise", "info tier"),
        ],
    )
    return base


# ===========================================================================
# 1. cmd_dogfood_aggregate -- short-code severity widened with canonical alias
# ===========================================================================


class TestDogfoodAggregateSeverityShortcodeParity:
    """W1005-followup-G Path A: dogfood-aggregate accepts canonical tokens."""

    # ---- Canonical-token parses cleanly (Pattern 3a fix) -----------------

    def test_dogfood_aggregate_min_severity_canonical_input_parses(self, evals_dir_three_tiers, cli_runner):
        """``--severity high`` parses cleanly (was click-usage-error 2 pre-fix).

        ``high`` is the W547 token that projects onto the H short-code via
        :data:`_CANONICAL_TO_SHORTCODE`. Pre-W1005-followup-G this exited 2.
        """
        result = invoke_cli(
            cli_runner,
            [
                "dogfood-aggregate",
                "--path",
                str(evals_dir_three_tiers),
                "--severity",
                "high",
            ],
            json_mode=True,
        )
        assert result.exit_code == 0, (
            f"dogfood-aggregate --severity high: expected exit 0 "
            f"(canonical token parses via W547 alias), got exit "
            f"{result.exit_code}; output: {result.output}"
        )
        env = _parse_json(result.output)
        # ``high`` projects onto ``H`` -- the filter keeps only H rows.
        severities = {f["sev"] for f in env["findings"]}
        assert severities == {"H"}, f"--severity high (projects to H) expected {{H}} only, got {severities}"
        assert REPO_ROOT.exists()  # drift guard: helper resolves correctly

    # ---- Short-code still accepted (back-compat) -------------------------

    def test_dogfood_aggregate_min_severity_shortcode_still_accepted(self, evals_dir_three_tiers, cli_runner):
        """``--severity H`` still parses (back-compat unchanged)."""
        result = invoke_cli(
            cli_runner,
            [
                "dogfood-aggregate",
                "--path",
                str(evals_dir_three_tiers),
                "--severity",
                "H",
            ],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = _parse_json(result.output)
        severities = {f["sev"] for f in env["findings"]}
        assert severities == {"H"}

    # ---- Case-insensitive parsing ----------------------------------------

    def test_dogfood_aggregate_min_severity_case_insensitive_lower(self, evals_dir_three_tiers, cli_runner):
        """``--severity h`` (lowercase short-code) parses cleanly."""
        result = invoke_cli(
            cli_runner,
            [
                "dogfood-aggregate",
                "--path",
                str(evals_dir_three_tiers),
                "--severity",
                "h",
            ],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = _parse_json(result.output)
        severities = {f["sev"] for f in env["findings"]}
        assert severities == {"H"}

    def test_dogfood_aggregate_min_severity_case_insensitive_upper(self, evals_dir_three_tiers, cli_runner):
        """``--severity HIGH`` (uppercase canonical) parses cleanly."""
        result = invoke_cli(
            cli_runner,
            [
                "dogfood-aggregate",
                "--path",
                str(evals_dir_three_tiers),
                "--severity",
                "HIGH",
            ],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = _parse_json(result.output)
        severities = {f["sev"] for f in env["findings"]}
        assert severities == {"H"}

    # ---- Floor semantic --------------------------------------------------

    def test_dogfood_aggregate_min_severity_floor_semantic(self, evals_dir_three_tiers, cli_runner):
        """``--severity M`` keeps H+M but drops L.

        Wait -- dogfood-aggregate's filter uses OR semantics (a wanted SET),
        not a floor comparator. ``--severity M`` keeps ONLY M rows. The
        analogous "M-and-worse" semantic is obtained by passing both H and M
        as repeated flags. This test pins the OR-set semantic, which is the
        existing dogfood-aggregate contract -- the widening only widens the
        INPUT vocab, not the filter algebra.
        """
        # Pass both H and M (OR semantics) -- exclude L.
        result = invoke_cli(
            cli_runner,
            [
                "dogfood-aggregate",
                "--path",
                str(evals_dir_three_tiers),
                "--severity",
                "H",
                "--severity",
                "M",
            ],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = _parse_json(result.output)
        severities = {f["sev"] for f in env["findings"]}
        assert severities == {"H", "M"}, f"--severity H --severity M expected {{H, M}}, got {severities}"
        assert "L" not in severities

    def test_dogfood_aggregate_min_severity_canonical_or_set(self, evals_dir_three_tiers, cli_runner):
        """Mixed canonical + short-code (OR set) drops L.

        ``--severity critical --severity warning`` projects to
        ``{H, M}`` via the alias map; the OR set drops L rows.
        """
        result = invoke_cli(
            cli_runner,
            [
                "dogfood-aggregate",
                "--path",
                str(evals_dir_three_tiers),
                "--severity",
                "critical",
                "--severity",
                "warning",
            ],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = _parse_json(result.output)
        severities = {f["sev"] for f in env["findings"]}
        assert severities == {"H", "M"}, (
            f"--severity critical --severity warning (project to {{H, M}}) expected {{H, M}}, got {severities}"
        )

    # ---- Emit vocab unchanged (one-way projection) -----------------------

    def test_dogfood_aggregate_emit_vocab_unchanged(self, evals_dir_three_tiers, cli_runner):
        """Findings in JSON output still surface short-code (H/M/L) strings.

        Pre-fix and post-fix the emit-side ``f["sev"]`` value is always
        H/M/L. The widening is one-way (INPUT expands, OUTPUT stays
        narrow). If this surfaces canonical tokens, the projection map
        leaked into the emit path -- the W1005-followup-G discipline broke.
        """
        result = invoke_cli(
            cli_runner,
            [
                "dogfood-aggregate",
                "--path",
                str(evals_dir_three_tiers),
                "--severity",
                "high",  # canonical input
            ],
            json_mode=True,
        )
        assert result.exit_code == 0, result.output
        env = _parse_json(result.output)
        for f in env["findings"]:
            assert f["sev"] in ("H", "M", "L"), (
                f"emit-side sev value {f['sev']!r} is NOT in the H/M/L "
                f"short-code vocab -- canonical projection leaked into emit"
            )
        # by_severity bucket keys also stay H/M/L.
        bucket_keys = set(env["summary"].get("by_severity", {}).keys())
        assert bucket_keys.issubset({"H", "M", "L"}), (
            f"by_severity bucket keys {bucket_keys} are NOT a subset of {{H, M, L}} -- emit vocab drifted"
        )

    # ---- Drift guard: every canonical token IS a valid Choice ------------

    def test_dogfood_aggregate_canonical_tokens_all_in_choice(self):
        """Every key of _CANONICAL_TO_SHORTCODE lives in the Click.Choice.

        Drift guard: if a contributor adds a canonical token to the alias
        map but forgets the Click.Choice widening, ``--severity <new>``
        would still trip usage error 2.
        """
        from roam.commands.cmd_dogfood_aggregate import (
            _CANONICAL_TO_SHORTCODE,
            dogfood_aggregate,
        )

        severity_opt = next(p for p in dogfood_aggregate.params if p.name == "severity_filter")
        choice_values = set(severity_opt.type.choices)
        for canonical_token in _CANONICAL_TO_SHORTCODE:
            assert canonical_token in choice_values, (
                f"_CANONICAL_TO_SHORTCODE includes {canonical_token!r} but "
                f"the --severity Click.Choice does not -- widening drifted "
                f"out of sync with the alias map. Choice: "
                f"{sorted(choice_values)}."
            )


# ===========================================================================
# 2. cmd_pr_bundle add risk -- short-code severity widened with canonical alias
# ===========================================================================


@pytest.fixture
def bundle_project(tmp_path, monkeypatch):
    """A minimal git repo so ``find_project_root()`` resolves correctly."""
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    subprocess.run(["git", "checkout", "-B", "test-branch"], cwd=proj, capture_output=True)
    monkeypatch.chdir(proj)
    return proj


def _read_bundle_file(proj: Path, branch: str = "test-branch") -> dict:
    """Read the on-disk bundle JSON for ``branch``."""
    safe = branch.replace("/", "__")
    path = proj / ".roam" / "pr-bundles" / f"{safe}.json"
    if not path.exists():
        path = proj / ".roam" / "pr-bundle.json"
    assert path.exists(), f"bundle file missing -- looked at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _bundle_invoke(runner: CliRunner, args: list[str]):
    """Invoke roam CLI; raise exceptions (don't swallow) for diagnostic clarity."""
    from roam.cli import cli

    return runner.invoke(cli, args, catch_exceptions=False)


class TestPrBundleRiskSeverityShortcodeParity:
    """W1005-followup-G Path A: pr-bundle add risk accepts canonical tokens."""

    # ---- Canonical-token parses cleanly (Pattern 3a fix) -----------------

    def test_pr_bundle_min_severity_canonical_input_parses(self, cli_runner, bundle_project):
        """``--severity high`` parses cleanly (was click-usage-error 2 pre-fix).

        ``high`` is the W547 token that projects onto H via
        :data:`_CANONICAL_TO_RISK_SHORTCODE`. Pre-W1005-followup-G this
        exited 2.
        """
        # Init the bundle first (precondition for add).
        result_init = _bundle_invoke(cli_runner, ["pr-bundle", "init", "--intent", "test-severity-widening"])
        assert result_init.exit_code == 0, result_init.output

        result = _bundle_invoke(
            cli_runner,
            [
                "pr-bundle",
                "add",
                "risk",
                "test risk via canonical token",
                "--severity",
                "high",
            ],
        )
        assert result.exit_code == 0, (
            f"pr-bundle add risk --severity high: expected exit 0 "
            f"(canonical token parses via W547 alias), got exit "
            f"{result.exit_code}; output: {result.output}"
        )
        bundle = _read_bundle_file(bundle_project)
        risks = bundle["risks"]
        assert len(risks) == 1
        # Emit-side severity is the H short-code (canonical projected).
        assert risks[0]["severity"] == "H", (
            f"risk severity emit expected 'H' (high -> H via alias map), got {risks[0]['severity']!r}"
        )

    # ---- Short-code still accepted (back-compat) -------------------------

    def test_pr_bundle_min_severity_shortcode_still_accepted(self, cli_runner, bundle_project):
        """``--severity H`` still parses (back-compat unchanged)."""
        _bundle_invoke(cli_runner, ["pr-bundle", "init", "--intent", "test-shortcode-backcompat"])
        result = _bundle_invoke(
            cli_runner,
            [
                "pr-bundle",
                "add",
                "risk",
                "test risk via short-code",
                "--severity",
                "H",
            ],
        )
        assert result.exit_code == 0, result.output
        bundle = _read_bundle_file(bundle_project)
        assert bundle["risks"][0]["severity"] == "H"

    # ---- Case-insensitive parsing ----------------------------------------

    def test_pr_bundle_min_severity_case_insensitive_lower(self, cli_runner, bundle_project):
        """``--severity h`` (lowercase short-code) parses cleanly."""
        _bundle_invoke(cli_runner, ["pr-bundle", "init", "--intent", "test-case-lower"])
        result = _bundle_invoke(
            cli_runner,
            ["pr-bundle", "add", "risk", "lower-case h risk", "--severity", "h"],
        )
        assert result.exit_code == 0, result.output
        bundle = _read_bundle_file(bundle_project)
        assert bundle["risks"][0]["severity"] == "H"

    def test_pr_bundle_min_severity_case_insensitive_upper(self, cli_runner, bundle_project):
        """``--severity HIGH`` (uppercase canonical) parses cleanly."""
        _bundle_invoke(cli_runner, ["pr-bundle", "init", "--intent", "test-case-upper"])
        result = _bundle_invoke(
            cli_runner,
            ["pr-bundle", "add", "risk", "upper-case HIGH risk", "--severity", "HIGH"],
        )
        assert result.exit_code == 0, result.output
        bundle = _read_bundle_file(bundle_project)
        # HIGH (canonical) projects onto H short-code.
        assert bundle["risks"][0]["severity"] == "H"

    # ---- Floor / mapping semantic ----------------------------------------

    def test_pr_bundle_severity_floor_semantic_medium_to_M(self, cli_runner, bundle_project):
        """``--severity medium`` projects to M (the mid-tier).

        The pr-bundle filter is NOT a floor (it stores one severity per risk
        row), but the alias map's mapping semantic is observable: passing
        ``--severity medium`` results in a stored severity of ``M``, passing
        ``--severity low`` results in ``L``, and ``--severity high`` results
        in ``H``. This pins the mapping decision (warning/medium -> M,
        info/low/note -> L).
        """
        # M = medium tier
        _bundle_invoke(cli_runner, ["pr-bundle", "init", "--intent", "test-medium-to-M"])
        result = _bundle_invoke(
            cli_runner,
            ["pr-bundle", "add", "risk", "medium tier risk", "--severity", "medium"],
        )
        assert result.exit_code == 0, result.output
        bundle = _read_bundle_file(bundle_project)
        assert bundle["risks"][0]["severity"] == "M", (
            f"--severity medium expected store 'M', got {bundle['risks'][0]['severity']!r}"
        )

    def test_pr_bundle_severity_floor_semantic_low_to_L(self, cli_runner, bundle_project):
        """``--severity low`` projects to L (the floor tier)."""
        _bundle_invoke(cli_runner, ["pr-bundle", "init", "--intent", "test-low-to-L"])
        result = _bundle_invoke(
            cli_runner,
            ["pr-bundle", "add", "risk", "low tier risk", "--severity", "low"],
        )
        assert result.exit_code == 0, result.output
        bundle = _read_bundle_file(bundle_project)
        assert bundle["risks"][0]["severity"] == "L"

    def test_pr_bundle_severity_critical_to_H(self, cli_runner, bundle_project):
        """``--severity critical`` projects to H (the blocker tier)."""
        _bundle_invoke(cli_runner, ["pr-bundle", "init", "--intent", "test-critical-to-H"])
        result = _bundle_invoke(
            cli_runner,
            [
                "pr-bundle",
                "add",
                "risk",
                "critical tier risk",
                "--severity",
                "critical",
            ],
        )
        assert result.exit_code == 0, result.output
        bundle = _read_bundle_file(bundle_project)
        assert bundle["risks"][0]["severity"] == "H"

    # ---- Emit vocab unchanged (one-way projection) -----------------------

    def test_pr_bundle_emit_vocab_unchanged(self, cli_runner, bundle_project):
        """Stored ``bundle["risks"][i]["severity"]`` is always one of H/M/L.

        Pre-fix and post-fix the emit-side severity is always H/M/L. The
        widening is one-way (INPUT expands, OUTPUT stays narrow). If this
        surfaces canonical tokens, the projection map leaked into the emit
        path -- the W1005-followup-G discipline broke and the
        risk_severity_distribution aggregator would silently miss every
        non-H/M/L bucket.
        """
        _bundle_invoke(cli_runner, ["pr-bundle", "init", "--intent", "test-emit-vocab"])
        # Add risks across all canonical tokens.
        for canonical, expected_shortcode in [
            ("critical", "H"),
            ("error", "H"),
            ("high", "H"),
            ("warning", "M"),
            ("medium", "M"),
            ("info", "L"),
            ("low", "L"),
            ("note", "L"),
        ]:
            result = _bundle_invoke(
                cli_runner,
                [
                    "pr-bundle",
                    "add",
                    "risk",
                    f"risk for {canonical}",
                    "--severity",
                    canonical,
                ],
            )
            assert result.exit_code == 0, result.output
        bundle = _read_bundle_file(bundle_project)
        for r in bundle["risks"]:
            assert r["severity"] in ("H", "M", "L"), (
                f"emit-side severity {r['severity']!r} is NOT in the H/M/L "
                f"short-code vocab -- canonical projection leaked into emit"
            )

    # ---- Drift guard: every canonical token IS a valid Choice ------------

    def test_pr_bundle_canonical_tokens_all_in_choice(self):
        """Every key of _CANONICAL_TO_RISK_SHORTCODE lives in the Click.Choice.

        Drift guard: if a contributor adds a canonical token to the alias
        map but forgets the Click.Choice widening, ``--severity <new>``
        would still trip usage error 2.
        """
        from roam.commands.cmd_pr_bundle import (
            _CANONICAL_TO_RISK_SHORTCODE,
            pr_bundle_add_risk,
        )

        severity_opt = next(p for p in pr_bundle_add_risk.params if p.name == "severity")
        choice_values = set(severity_opt.type.choices)
        for canonical_token in _CANONICAL_TO_RISK_SHORTCODE:
            assert canonical_token in choice_values, (
                f"_CANONICAL_TO_RISK_SHORTCODE includes {canonical_token!r} "
                f"but the --severity Click.Choice does not -- widening "
                f"drifted out of sync with the alias map. Choice: "
                f"{sorted(choice_values)}."
            )

    def test_pr_bundle_canonical_projections_into_shortcode_vocab(self):
        """Every value in _CANONICAL_TO_RISK_SHORTCODE is a valid H/M/L slot.

        Polarity guard: a future contributor extending the canonical vocab
        must also map the new token onto one of the H/M/L slots, NOT
        introduce a fourth slot (which would silently break the
        risk_severity_distribution bucket-key contract).
        """
        from roam.commands.cmd_pr_bundle import (
            _CANONICAL_TO_RISK_SHORTCODE,
            _RISK_VALID_SHORTCODES,
        )

        for canonical, shortcode in _CANONICAL_TO_RISK_SHORTCODE.items():
            assert shortcode in _RISK_VALID_SHORTCODES, (
                f"_CANONICAL_TO_RISK_SHORTCODE[{canonical!r}] -> "
                f"{shortcode!r} is NOT a valid short-code slot "
                f"({_RISK_VALID_SHORTCODES})."
            )
