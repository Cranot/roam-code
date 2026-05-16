"""W1245 — Pattern-2 variant-D resolution disclosure on ``roam pr-bundle add affected``.

W1267 audit flagged ``pr-bundle add affected`` NON-COMPLIANT on the
fuzzy-resolution axis. The W20.5 substrate already disclosed
``no_db`` / ``not_found`` / ``lookup_failed`` ghosts via
``unresolved_affected_symbol`` + ``unresolved_affected_state`` --
but the LIKE-fallback tier silently absorbed: when an agent typed
``add affected useFoo`` and the resolver landed on ``useFooBar`` via
the LIKE substring fallback, the bundle stamped the resolved metadata
(kind / file_path / blast_radius) and shipped ``resolution_state=ok``
indistinguishable from a fully-resolved success.

W1245 propagation: ``_resolve_symbol_in_index`` now reads
``_resolution_tier`` off the find_symbol return; the ``fuzzy``
tier promotes ``resolution_state="fuzzy_resolution"`` and the envelope
emits the canonical Pattern-2 variant-D disclosure
(``resolution=fuzzy`` + ``partial_success=True`` + verdict suffix).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


def _invoke(cli_runner, args, **kw):
    from roam.cli import cli

    return cli_runner.invoke(cli, args, catch_exceptions=False, **kw)


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def bundle_indexed_project(project_factory, monkeypatch):
    """A git repo with an indexed Python file so the resolver has a real
    ``handle_payment_event`` symbol to walk the 3-tier chain against.
    """
    proj = project_factory(
        {
            "src/core.py": (
                "def handle_payment_event(event):\n"
                "    return event\n"
                "\n"
                "def caller():\n"
                "    return handle_payment_event(None)\n"
            ),
        }
    )
    # Pin branch for deterministic bundle path.
    subprocess.run(
        ["git", "checkout", "-B", "w1245-branch"],
        cwd=proj,
        capture_output=True,
    )
    monkeypatch.chdir(proj)
    return proj


class TestPrBundleAddAffectedResolution:
    """Resolution disclosure on ``roam pr-bundle add affected <symbol>``."""

    def test_exact_match_emits_ok_state_no_partial(self, bundle_indexed_project, cli_runner) -> None:
        """Exact-name match -> ``resolution_state="ok"`` and no fuzzy disclosure."""
        _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Refactor"])
        result = _invoke(
            cli_runner,
            ["--json", "pr-bundle", "add", "affected", "handle_payment_event"],
        )
        assert result.exit_code == 0, result.output
        raw = getattr(result, "stdout", None) or result.output
        data = json.loads(raw)
        # ``ok`` state must NOT propagate a Pattern-2 variant-D disclosure
        # (the resolver hit exact-name on tier 1 or 2). The W20.5
        # ``unresolved_affected_*`` keys also stay absent.
        assert data["summary"].get("resolution") not in {"fuzzy", "unresolved"}
        assert "[fuzzy resolution]" not in data["summary"]["verdict"]
        # The persisted record stamps the W20.5 resolution_state.
        bundle_path = bundle_indexed_project / ".roam" / "pr-bundles" / "w1245-branch.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        affected = bundle["affected_symbols"][0]
        assert affected["resolution_state"] == "ok"

    def test_fuzzy_match_emits_fuzzy_disclosure(self, bundle_indexed_project, cli_runner) -> None:
        """LIKE-fallback match -> ``resolution=fuzzy`` + verdict suffix +
        ``resolution_state="fuzzy_resolution"`` on the persisted record.
        """
        _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Refactor"])
        # ``payment_event`` is a substring of ``handle_payment_event`` so the
        # 3-tier resolver falls through qualified -> simple -> LIKE-fallback.
        result = _invoke(
            cli_runner,
            ["--json", "pr-bundle", "add", "affected", "payment_event"],
        )
        assert result.exit_code == 0, result.output
        raw = getattr(result, "stdout", None) or result.output
        data = json.loads(raw)
        # W1245 Pattern-2 variant-D disclosure.
        assert data["summary"]["resolution"] == "fuzzy"
        assert data["summary"]["partial_success"] is True
        assert data["resolution"] == "fuzzy"
        assert data["partial_success"] is True
        # LAW 6: the verdict alone signals the degradation.
        assert "[fuzzy resolution]" in data["summary"]["verdict"]
        # Persisted record stamps the new resolution_state.
        bundle_path = bundle_indexed_project / ".roam" / "pr-bundles" / "w1245-branch.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        affected = bundle["affected_symbols"][0]
        assert affected["resolution_state"] == "fuzzy_resolution"

    def test_unresolved_emits_unresolved_disclosure(self, bundle_indexed_project, cli_runner) -> None:
        """Total miss -> ``resolution=unresolved`` + the pre-W1245
        ``unresolved_affected_symbol`` keys (additive — W20.5 substrate
        preserved alongside the new variant-D shape).
        """
        _invoke(cli_runner, ["pr-bundle", "init", "--intent", "Refactor"])
        result = _invoke(
            cli_runner,
            ["--json", "pr-bundle", "add", "affected", "ghost_zzz_xyz"],
        )
        assert result.exit_code == 0, result.output
        raw = getattr(result, "stdout", None) or result.output
        data = json.loads(raw)
        # W1245 Pattern-2 variant-D disclosure (new).
        assert data["summary"]["resolution"] == "unresolved"
        assert data["summary"]["partial_success"] is True
        assert data["resolution"] == "unresolved"
        assert data["partial_success"] is True
        # W20.5 substrate keys (preserved).
        assert data["summary"]["unresolved_affected_symbol"] == "ghost_zzz_xyz"
        assert data["summary"]["unresolved_affected_state"] == "not_found"
