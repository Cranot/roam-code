"""Tests for the ROAM_RUN_ID side-car write into ``.roam/responses/``.

Closes the W9.1 gap: ``roam pr-bundle --auto-collect`` only saw envelopes that
the MCP handle-off had written. With this hook, CLI invocations of
``roam --json <cmd>`` ALSO drop a copy of their envelope into
``.roam/responses/<sha>.json`` whenever ``ROAM_RUN_ID`` is set — so auto-collect
finds them later.

These tests pin down:
  - the env-gate semantics (no ``ROAM_RUN_ID`` ⇒ no write)
  - the exclusion list (runs / memory / constitution / pr-bundle don't echo)
  - content-hash dedup (re-running same cmd doesn't dupe files)
  - silent-failure guarantee (a broken filesystem must NOT crash the command)
  - end-to-end: pr-bundle auto-collect actually consumes CLI-written envelopes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, parse_json_output  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal git-rooted project so ``find_project_root()`` resolves to tmp.

    We chdir into it so the formatter's helper writes to *this* project's
    ``.roam/responses/`` instead of the surrounding roam-code repo.
    """
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def hello():\n    return 'hi'\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    # Ensure no stray ROAM_RUN_ID leaks from the harness.
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    return proj


def _responses_dir(proj: Path) -> Path:
    return proj / ".roam" / "responses"


def _list_responses(proj: Path) -> list[Path]:
    d = _responses_dir(proj)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".json" and p.is_file())


# ---------------------------------------------------------------------------
# Unit tests against ``json_envelope`` directly
# ---------------------------------------------------------------------------


def test_no_run_id_no_write(project, monkeypatch):
    """Without ``ROAM_RUN_ID`` the envelope must roundtrip without side-effects."""
    from roam.output.formatter import json_envelope

    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    env = json_envelope("health", summary={"verdict": "ok", "score": 88})

    # Envelope dict is valid …
    assert env["command"] == "health"
    assert env["schema"] == "roam-envelope-v1"
    # … but no file was written.
    assert _list_responses(project) == []


def test_with_run_id_writes_envelope(project, monkeypatch):
    """With ``ROAM_RUN_ID`` set the envelope is dropped into .roam/responses/."""
    from roam.output.formatter import json_envelope

    monkeypatch.setenv("ROAM_RUN_ID", "test-run-abc")
    env = json_envelope("health", summary={"verdict": "ok", "score": 88})

    files = _list_responses(project)
    assert len(files) == 1, f"expected exactly one response file, got: {files}"
    assert files[0].name.startswith("health_")
    assert files[0].suffix == ".json"

    # File content matches the envelope content for the high-signal fields.
    on_disk = json.loads(files[0].read_text(encoding="utf-8"))
    assert on_disk["command"] == "health"
    assert on_disk["schema"] == "roam-envelope-v1"
    assert on_disk["summary"]["verdict"] == env["summary"]["verdict"]


def test_excluded_commands_skip_write(project, monkeypatch):
    """``runs-log`` / ``memory-add`` / ``pr-bundle-emit`` etc. must NEVER echo."""
    from roam.output.formatter import json_envelope

    monkeypatch.setenv("ROAM_RUN_ID", "test-run-xyz")

    excluded = [
        "runs-start",
        "runs-log",
        "runs-end",
        "runs-list",
        "runs-show",
        "memory-add",
        "memory-list",
        "memory-relevant",
        "constitution-init",
        "constitution-check",
        "constitution-show",
        "constitution-apply",
        "constitution-where",
        "pr-bundle",
        "pr-bundle-init",
        "pr-bundle-emit",
        "pr-bundle-validate",
        "pr-bundle-add",
        "pr-bundle-set",
        "pr-bundle-set-intent",
        "pr-bundle-add-affected",
        "pr-bundle-add-risk",
        "pr-bundle-add-test-required",
        "pr-bundle-add-test-run",
        "pr-bundle-add-non-goal",
        "pr-bundle-add-context-cmd",
        "pr-bundle-add-context-symbol",
        "pr-bundle-add-context-file",
    ]
    for cmd in excluded:
        json_envelope(cmd, summary={"verdict": "ok"})

    assert _list_responses(project) == [], (
        "excluded commands must NOT write to .roam/responses/ "
        "(would create feedback loops or double-count in pr-bundle auto-collect)"
    )


def test_write_failure_does_not_raise(project, monkeypatch):
    """A broken filesystem must NOT propagate — best-effort guarantee."""
    from roam.output.formatter import json_envelope

    monkeypatch.setenv("ROAM_RUN_ID", "test-run-boom")

    # Force write_text to raise on every call.
    original_write_text = Path.write_text

    def boom(self, *args, **kwargs):
        if ".roam" in str(self) and "responses" in str(self):
            raise OSError("simulated disk failure")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom)

    # Must NOT raise: the side-car write is best-effort.
    env = json_envelope("health", summary={"verdict": "ok"})
    assert env["command"] == "health"  # parent command still got its envelope


def test_dedup_via_content_hash(project, monkeypatch):
    """Two identical envelope calls must produce a single file (content-hash dedup)."""
    from roam.output.formatter import json_envelope

    monkeypatch.setenv("ROAM_RUN_ID", "test-run-dedup")

    # The envelope includes _meta.timestamp (non-deterministic by design), but
    # _meta lives inside the envelope so the hash WILL differ between calls.
    # Dedup is therefore content-aware: same logical envelope → same hash only
    # when serialised bytes are identical. We assert the file is overwritten,
    # not duplicated.
    #
    # In practice, the timestamp shifts second-by-second. We monkey-patch the
    # timestamp helper so both calls produce the same envelope bytes — which
    # is the realistic case for a fast-running CLI invocation.
    from roam.output import formatter as fmt

    monkeypatch.setattr(fmt, "datetime", _FrozenDatetime, raising=True)

    json_envelope("health", summary={"verdict": "ok", "score": 42})
    json_envelope("health", summary={"verdict": "ok", "score": 42})

    files = _list_responses(project)
    assert len(files) == 1, f"two identical envelopes must dedup to ONE file via content hash, got: {files}"


class _FrozenDatetime:
    """Stand-in for ``datetime`` that returns a fixed UTC instant.

    Used to freeze ``_meta.timestamp`` so we can verify content-hash dedup.
    Only the methods/attributes that ``json_envelope`` touches are stubbed.
    """

    from datetime import datetime as _real_datetime
    from datetime import timezone as _real_timezone

    _FIXED = _real_datetime(2026, 5, 13, 12, 0, 0, tzinfo=_real_timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._FIXED

    # Pass-through attributes the module reads from `datetime`.
    timezone = _real_timezone


def test_writes_when_active_bundle_present(project, monkeypatch):
    """W15.2 followup: a ``.roam/pr-bundles/*.json`` triggers the write
    even when ROAM_RUN_ID is unset.

    Closes the natural workflow ``pr-bundle init → preflight → pr-bundle
    emit --auto-collect`` for agents who never opened a run.
    """
    from roam.output.formatter import json_envelope

    # No run id env -- the legacy trigger is absent.
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    # Drop a bundle on disk so the new trigger fires.
    bundles = project / ".roam" / "pr-bundles"
    bundles.mkdir(parents=True, exist_ok=True)
    (bundles / "test-branch.json").write_text('{"schema": "roam-pr-bundle"}', encoding="utf-8")

    env = json_envelope("health", summary={"verdict": "ok", "score": 88})

    files = _list_responses(project)
    assert len(files) == 1, f"expected exactly one response file from the bundle-signal trigger, got: {files}"
    assert files[0].name.startswith("health_")
    # The envelope content is intact.
    on_disk = json.loads(files[0].read_text(encoding="utf-8"))
    assert on_disk["command"] == "health"
    assert on_disk["summary"]["verdict"] == env["summary"]["verdict"]


def test_writes_when_both_signals_present(project, monkeypatch):
    """ROAM_RUN_ID set AND a bundle exists → one write, not two.

    Content-hash dedup means both triggers still produce exactly one file
    per logically-identical envelope.
    """
    from roam.output import formatter as fmt
    from roam.output.formatter import json_envelope

    monkeypatch.setenv("ROAM_RUN_ID", "test-both-signals")
    bundles = project / ".roam" / "pr-bundles"
    bundles.mkdir(parents=True, exist_ok=True)
    (bundles / "main.json").write_text('{"schema": "roam-pr-bundle"}', encoding="utf-8")
    # Freeze timestamp so both invocations produce identical bytes →
    # content-hash dedup collapses them to a single file.
    monkeypatch.setattr(fmt, "datetime", _FrozenDatetime, raising=True)

    json_envelope("health", summary={"verdict": "ok", "score": 7})
    json_envelope("health", summary={"verdict": "ok", "score": 7})

    files = _list_responses(project)
    assert len(files) == 1, f"both signals present + identical envelopes should still dedup to one file, got: {files}"


def test_no_signals_no_write_even_with_bundle_dir_empty(project, monkeypatch):
    """Empty ``.roam/pr-bundles/`` directory does NOT trigger the write.

    Guards against treating an empty directory the same as a populated one —
    only the presence of an actual bundle file counts.
    """
    from roam.output.formatter import json_envelope

    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    bundles = project / ".roam" / "pr-bundles"
    bundles.mkdir(parents=True, exist_ok=True)
    # Empty directory — no bundle JSON inside.

    json_envelope("health", summary={"verdict": "ok"})

    assert _list_responses(project) == [], (
        "empty pr-bundles/ directory must NOT trigger the write — only a real bundle file is a valid signal"
    )


def test_non_envelope_dict_is_not_written(project, monkeypatch):
    """The helper must refuse non-envelope dicts even when ROAM_RUN_ID is set.

    Guards against a future refactor accidentally piping arbitrary dicts
    through the same code path.
    """
    from roam.output.formatter import _write_response_to_responses_dir

    monkeypatch.setenv("ROAM_RUN_ID", "test-run-guard")

    # Missing schema marker → must be rejected.
    _write_response_to_responses_dir({"command": "health", "summary": {}})
    # Wrong schema marker → must be rejected.
    _write_response_to_responses_dir({"schema": "something-else", "command": "health", "summary": {}})
    # Empty command → must be rejected.
    _write_response_to_responses_dir({"schema": "roam-envelope-v1", "command": "", "summary": {}})

    assert _list_responses(project) == []


# ---------------------------------------------------------------------------
# End-to-end: pr-bundle auto-collect consumes CLI-written envelopes
# ---------------------------------------------------------------------------


@pytest.mark.xdist_group("cli_responses_e2e")
def test_pr_bundle_auto_collect_consumes_cli_responses(project, monkeypatch):
    """End-to-end smoke: a CLI envelope written via ROAM_RUN_ID must be folded
    in by ``pr-bundle emit --auto-collect``.

    The whole point of W9.1: CLI users running ``roam --json preflight X``
    directly produced no envelopes in ``.roam/responses/``, so the killer
    auto-collect feature always saw empty. This test asserts the gap is closed.
    """
    from roam.cli import cli
    from roam.output.formatter import json_envelope

    monkeypatch.setenv("ROAM_RUN_ID", "test-e2e-run")

    runner = CliRunner()

    # Step 1: simulate a roam --json health invocation by directly emitting an
    # envelope (we avoid invoking the full health command because it requires
    # an indexed DB and we want a fast, deterministic test).
    json_envelope(
        "health",
        summary={"verdict": "Healthy 88/100 with 0 cycles", "score": 88},
        affected_symbols=[
            {"name": "hello", "kind": "function", "file": "main.py", "blast_radius": 1},
        ],
        risks=[{"id": "R1", "severity": "L", "description": "low-impact churn"}],
    )

    assert len(_list_responses(project)) >= 1, (
        "ROAM_RUN_ID was set but no envelope was written — the W9.1 hook is broken"
    )

    # Step 2: init the bundle.
    result = runner.invoke(
        cli,
        ["--json", "pr-bundle", "init", "--intent", "test the W9.1 closure"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"pr-bundle init failed: {result.output}"

    # Step 3: emit with auto-collect (the default).
    result = runner.invoke(
        cli,
        ["--json", "pr-bundle", "emit"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"pr-bundle emit failed: {result.output}"

    payload = parse_json_output(result, command="pr-bundle-emit")
    # W15.2 envelope reshape: auto_collect now under summary, not top-level.
    auto = payload["summary"].get("auto_collect") or {}
    assert auto.get("enabled") is True, f"auto_collect not enabled in: {payload}"
    assert auto.get("envelopes_scanned", 0) >= 1, (
        f"auto-collect saw zero envelopes — W9.1 still open. auto_collect={auto}"
    )
    # The health envelope should have contributed at least one of these:
    # - commands_run: "roam health"
    # - affected_symbols: hello
    # - risks: low-impact churn
    contributed = auto.get("commands_run", 0) + auto.get("affected_symbols", 0) + auto.get("risks", 0)
    assert contributed >= 1, f"auto-collect scanned envelopes but folded nothing in — harvester broken: {auto}"
