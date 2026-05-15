"""Alias-deprecation contract tests.

Seven legacy CLI aliases graduated from ``_INTENTIONALLY_UNCATEGORISED``
in ``test_surface_consistency.py`` into ``_DEPRECATED_COMMANDS`` in
``src/roam/cli.py`` (W3.3 / SYNTHESIS Rank 18). They continue to work —
this is *not* a breaking change — but every invocation now emits a
deprecation note on stderr, and JSON envelopes carry the same notice
under ``summary.deprecation_warning`` so JSON-only consumers (CI, MCP
clients) can detect the rename mechanically.

Behaviour locked here:

* invoking a deprecated alias prints a ``DEPRECATION: ...`` line on stderr
* exit code is unchanged (no blocking, no exit-5)
* canonical command still executes (same module + function tuple as the
  rename target)
* ``--json <alias>`` includes ``summary.deprecation_warning`` matching the
  stderr text
* canonical commands (``algo``, ``uses``, ``weather``, ``trends``,
  ``understand``) emit no deprecation warning
* every entry in ``_DEPRECATED_COMMANDS`` resolves to a real ``_COMMANDS``
  entry (covered by ``test_surface_consistency``; cross-checked here too)
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS, cli


# The seven aliases under test, with their canonical replacement and the
# CLI module/function tuple the canonical name resolves to. We assert that
# the alias resolves to the SAME tuple so it's a true alias (not a divergent
# fork that happens to share a name).
_SEVEN_ALIASES = {
    "digest": "trends",
    "math": "algo",
    "refs": "uses",
    "snapshot": "trends",
    "trend": "trends",
    "onboard": "understand",
    "churn": "weather",
}


@pytest.fixture
def cli_runner_split_stderr():
    """CliRunner with stderr captured separately from stdout.

    Required for ``test_deprecated_alias_emits_warning_on_stderr`` because
    the deprecation note must NOT leak into stdout (JSON consumers parse
    stdout and a leading non-JSON line would crash the loader).
    """
    # Click 8.3+ removed mix_stderr; result.stdout and result.stderr are
    # always separated. The _result_stderr / _result_stdout helpers below
    # paper over the older-Click merged-output path if anyone runs there.
    return CliRunner()


def _result_stderr(result) -> str:
    """Return stderr from a CliRunner result, robust across Click versions."""
    # Click >= 8.3 exposes result.stderr unconditionally. Older Click only
    # exposes it when mix_stderr=False — fall back to result.output which is
    # merged stdout+stderr on older versions.
    try:
        val = getattr(result, "stderr", None)
        if val:
            return val
    except (AttributeError, ValueError):
        pass
    return result.output or ""


def _result_stdout(result) -> str:
    """Return stdout-only from a CliRunner result, robust across Click versions.

    Click 8.3 removed the ``mix_stderr`` kwarg and always exposes ``stdout``
    and ``stderr`` separately. Older Click merged everything into
    ``output``; we read ``stdout`` when available and fall back to
    ``output`` otherwise. This matters for JSON parsing — a deprecation
    note on stderr must not contaminate the stdout stream the test
    feeds to ``json.loads``.
    """
    val = getattr(result, "stdout", None)
    if val is not None:
        return val
    return result.output or ""


# ---------------------------------------------------------------------------
# 1. Deprecation warning is emitted on stderr, not stdout
# ---------------------------------------------------------------------------


def test_deprecated_alias_emits_warning_on_stderr(cli_runner_split_stderr):
    """Invoking a deprecated alias prints ``DEPRECATION: ...`` to stderr.

    ``roam math --help`` is the canonical harmless probe — ``--help`` exits
    cleanly with code 0 on any Click command, so the test doesn't depend on
    a working index.
    """
    result = cli_runner_split_stderr.invoke(cli, ["math", "--help"])
    # --help always exits 0 regardless of whether the canonical command
    # would succeed under the current cwd.
    assert result.exit_code == 0, (
        f"`roam math --help` exited {result.exit_code}; stdout={result.output!r}; "
        f"stderr={_result_stderr(result)!r}"
    )
    stderr = _result_stderr(result)
    assert "DEPRECATION" in stderr, (
        f"Expected 'DEPRECATION' on stderr when invoking deprecated alias 'math'; "
        f"got stderr={stderr!r}"
    )
    assert "math" in stderr and "algo" in stderr, (
        f"Deprecation notice should name both the alias ('math') and the "
        f"replacement ('algo'); got stderr={stderr!r}"
    )
    # The canonical command's help text mentions itself ("algo" or the
    # docstring of cmd_math.math_cmd). Verify --help still ran rather than
    # being aborted by the deprecation handling.
    stdout = _result_stdout(result)
    assert "Usage:" in stdout or "Options:" in stdout, (
        f"`roam math --help` should still render the canonical command's "
        f"help text on stdout; got stdout={stdout!r}"
    )


# ---------------------------------------------------------------------------
# 2. Deprecation is non-blocking — canonical command still runs
# ---------------------------------------------------------------------------


def test_deprecated_alias_does_not_block_execution(cli_runner_split_stderr, tmp_path):
    """``roam <alias> --help`` exits 0 even with a deprecation note.

    The deprecation surface is informational. It MUST NOT raise, set a
    non-zero exit code, or skip execution. We probe with ``--help`` because
    every Click command supports it without needing a roam index.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        for alias in _SEVEN_ALIASES:
            result = cli_runner_split_stderr.invoke(cli, [alias, "--help"])
            assert result.exit_code == 0, (
                f"`roam {alias} --help` should exit 0 (deprecation is "
                f"non-blocking); got exit={result.exit_code}, "
                f"stderr={_result_stderr(result)!r}"
            )
            # Sanity: --help output is non-empty, proving the canonical
            # command actually ran (vs being short-circuited by the
            # deprecation handler).
            stdout = _result_stdout(result)
            assert "Usage:" in stdout or "Options:" in stdout, (
                f"`roam {alias} --help` did not render canonical command's "
                f"help on stdout — the canonical command did not run. "
                f"stdout={stdout[:200]!r}"
            )
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 3. JSON envelope carries the deprecation warning under summary
# ---------------------------------------------------------------------------


def test_deprecated_alias_in_json_mode_carries_warning_in_envelope(
    cli_runner_split_stderr, indexed_project, monkeypatch
):
    """``roam --json <alias>`` includes ``summary.deprecation_warning``.

    JSON consumers (CI pipelines, MCP clients) don't see stderr, so the
    stderr note alone is insufficient. The envelope MUST carry the same
    warning text under ``summary.deprecation_warning`` for them.

    We exercise the smallest deprecated alias that works against the
    standard ``indexed_project`` fixture — ``churn`` -> ``weather``, which
    runs as a simple JSON-emitting health check.
    """
    monkeypatch.chdir(indexed_project)
    result = cli_runner_split_stderr.invoke(cli, ["--json", "churn"])
    # Non-zero exit codes are tolerated here (e.g. weather may report a
    # health verdict via a documented exit code); the envelope is what
    # matters.
    stdout = _result_stdout(result)
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"`roam --json churn` did not produce valid JSON on stdout: "
            f"{exc}\nstdout={stdout[:500]!r}\nstderr={_result_stderr(result)[:500]!r}"
        )

    summary = data.get("summary") or {}
    assert "deprecation_warning" in summary, (
        f"`roam --json churn` envelope is missing summary.deprecation_warning. "
        f"Got summary keys: {sorted(summary.keys())}"
    )
    warning = summary["deprecation_warning"]
    assert "DEPRECATION" in warning, (
        f"summary.deprecation_warning should be a 'DEPRECATION: ...' string; "
        f"got {warning!r}"
    )
    assert "churn" in warning and "weather" in warning, (
        f"summary.deprecation_warning should name alias ('churn') and "
        f"replacement ('weather'); got {warning!r}"
    )


# ---------------------------------------------------------------------------
# 4. Canonical commands do not emit a deprecation warning
# ---------------------------------------------------------------------------


def test_canonical_command_emits_no_deprecation(cli_runner_split_stderr, tmp_path):
    """Invoking the canonical name (e.g. ``algo``) must produce no notice.

    A false-positive deprecation on canonical commands would mis-train
    agents into rewriting valid invocations. We check `--help` (always
    exits 0, no index required) for every canonical replacement.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        for canonical in set(_SEVEN_ALIASES.values()):
            result = cli_runner_split_stderr.invoke(cli, [canonical, "--help"])
            assert result.exit_code == 0, (
                f"`roam {canonical} --help` exited {result.exit_code}; "
                f"stderr={_result_stderr(result)!r}"
            )
            stderr = _result_stderr(result)
            assert "DEPRECATION" not in stderr, (
                f"Canonical command `{canonical}` unexpectedly emitted a "
                f"deprecation notice on stderr: {stderr!r}"
            )
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 5. All seven aliases land in _DEPRECATED_COMMANDS with a working pointer
# ---------------------------------------------------------------------------


def test_all_seven_aliases_resolve():
    """Each of the seven aliases has a ``_DEPRECATED_COMMANDS`` entry.

    The entry must (a) name the correct replacement and (b) point at the
    same ``(module, function)`` tuple in ``_COMMANDS`` so the alias is a
    true rename and not a divergent fork.
    """
    missing = sorted(set(_SEVEN_ALIASES) - set(_DEPRECATED_COMMANDS))
    assert not missing, (
        f"{len(missing)} alias(es) not in _DEPRECATED_COMMANDS: {missing}. "
        f"Each of {sorted(_SEVEN_ALIASES)} must be marked deprecated."
    )

    wrong_replacement = []
    not_in_commands = []
    target_mismatch = []
    for alias, expected_canonical in _SEVEN_ALIASES.items():
        record = _DEPRECATED_COMMANDS[alias]
        # Bare-string or dict — both are valid record shapes per cli.py.
        replacement = record if isinstance(record, str) else record.get("replacement")
        if replacement != expected_canonical:
            wrong_replacement.append((alias, replacement, expected_canonical))
            continue
        # Both alias and replacement must be invokable.
        if alias not in _COMMANDS:
            not_in_commands.append(alias)
            continue
        if replacement not in _COMMANDS:
            not_in_commands.append(replacement)
            continue
        # Alias must be a true rename: same (module, function) tuple.
        if _COMMANDS[alias] != _COMMANDS[replacement]:
            target_mismatch.append((alias, _COMMANDS[alias], replacement, _COMMANDS[replacement]))

    assert not wrong_replacement, (
        "Deprecation record points at the wrong replacement:\n  "
        + "\n  ".join(f"{a}: {got!r} (expected {want!r})" for a, got, want in wrong_replacement)
    )
    assert not not_in_commands, (
        "Deprecated alias or replacement is missing from _COMMANDS:\n  "
        + "\n  ".join(sorted(set(not_in_commands)))
    )
    assert not target_mismatch, (
        "Deprecated alias points at a different (module, function) than its replacement:\n  "
        + "\n  ".join(
            f"{a} -> {at} vs {r} -> {rt}" for a, at, r, rt in target_mismatch
        )
    )
