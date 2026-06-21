"""W196 regression — the grep-replication probe must pass a task-mentioned
literal pattern as a POSITIONAL, never as a Click option.

Attack: a task literal shaped like ``--patterns-from=/etc/passwd`` would, if
forwarded ahead of a ``--`` separator, be parsed by ``roam grep`` as the
``--patterns-from`` option — reading an attacker-named local file as the
pattern list instead of searching for the literal string.

Defense (compiler.py:_grep_one_pattern): fixed options FIRST, then ``--``,
then the untrusted pattern. This test pins the argv shape so the ordering
can never silently regress.
"""

from __future__ import annotations

import roam.plan.compiler as compiler


def _capture_run_roam(monkeypatch):
    """Patch _run_roam to record argv and return an empty (non-dict) result so
    _grep_one_pattern short-circuits after dispatch."""
    seen: dict = {}

    def fake(args, cwd, timeout=8.0, detail=False):
        seen["args"] = list(args)
        return None  # short-circuit; we only assert on argv

    monkeypatch.setattr(compiler, "_run_roam", fake)
    return seen


def test_grep_pattern_passed_after_double_dash(monkeypatch):
    seen = _capture_run_roam(monkeypatch)
    compiler._grep_one_pattern("--patterns-from=/etc/passwd", "/tmp/repo")
    args = seen["args"]

    # The pattern is the final positional and is preceded by `--`.
    assert args[-1] == "--patterns-from=/etc/passwd"
    assert args[-2] == "--"
    dd = args.index("--")
    assert args[dd + 1 :] == ["--patterns-from=/etc/passwd"]


def test_fixed_options_precede_the_separator(monkeypatch):
    seen = _capture_run_roam(monkeypatch)
    compiler._grep_one_pattern("anything", "/tmp/repo")
    args = seen["args"]

    # subcommand is first; every fixed option sits before `--`.
    # `--fixed-string` pins W196 literal-mode: _extract_grep_patterns yields
    # literal task mentions, so the engine must not widen them via regex.
    assert args[0] == "grep"
    dd = args.index("--")
    head = args[:dd]
    assert "-n" in head and "--source-only" in head and "--fixed-string" in head


def test_leading_dash_pattern_is_not_an_option(monkeypatch):
    """A bare `-rf`-style literal must also land as a positional."""
    seen = _capture_run_roam(monkeypatch)
    compiler._grep_one_pattern("-rf", "/tmp/repo")
    args = seen["args"]
    assert args[-1] == "-rf"
    assert args[args.index("--") + 1] == "-rf"
