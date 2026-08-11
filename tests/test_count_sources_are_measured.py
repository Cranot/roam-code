"""The doc-count gates must FAIL when the truth moves, not only when the
generator disagrees with itself.

Why this file exists
--------------------
``dev/build_readme_counts.py`` is the count-drift killer, but two of the
numbers it published were never *derived* from anything:

* the language count was the literal ``28`` typed into four f-strings, and
  ``Counts`` had no ``languages`` field at all;
* the SARIF command count lived only as hand-typed prose in README.md,
  outside every auto-count marker.

The consequence, measured before this file landed: injecting ``26 languages``
and ``38 SARIF commands`` (real: 28 and 37) into README.md passed the entire
``doc-hygiene`` CI job and all 121 doc-guard tests. The language literal was
worse than merely unguarded — because the generator wrote ``28`` and the
README said ``28``, ``--check`` reported green *by construction*. Growing the
extractor registry to 29 languages left every published surface claiming 28
and every gate passing.

So the tests here are deliberately not "does the generator agree with the
docs". They are:

1. **Truth-moves tests** — move the SOURCE (patch the language registry,
   grow the ``_SARIF_CONSUMERS`` tuple, change the command-module census)
   and assert the check turns RED. A check that cannot fail on a moved
   source is a mirror, and a mirror is the defect it pretends to guard.
2. **Injection tests** — write the exact wrong numbers that used to pass
   into a throwaway copy of README.md and assert the check fails *and names
   the file*.
3. **A literal guard** — assert the generator no longer hardcodes a
   language or SARIF count, so the mirror cannot grow back.

Parallel safety: every test operates on a ``tmp_path`` shadow of the
count-bearing files (the harness ``tests/test_auto_count_script.py``
established) and runs the writer in-process with ``write=False``. The real
working tree is never modified.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SRC = ROOT / "src"
BUILD_SCRIPT = ROOT / "dev" / "build_readme_counts.py"
SYNC_SCRIPT = ROOT / "scripts" / "sync_surface_counts.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# Same shadow set as tests/test_auto_count_script.py: the truth inputs the
# writer reads plus every count-bearing file it may rewrite.
_SHADOW_RELATIVE_PATHS: tuple[str, ...] = (
    "pyproject.toml",
    "src/roam/cli.py",
    "src/roam/mcp_server.py",
    "README.md",
    "CLAUDE.md",
    "llms-install.md",
    "AGENTS.md",
    "docs/mcp-tools.md",
    "src/roam/mcp-server-card.json",
    "templates/distribution/landing-page/.well-known/mcp-server-card.json",
    "templates/distribution/landing-page/.well-known/mcp/server-card.json",
    "templates/distribution/landing-page/.well-known/mcp-server-card",
    "tests/test_mcp_server_card_hash.py",
)


def _load(name: str, path: Path):
    """Import a dev/scripts module by path.

    Registered in ``sys.modules`` before ``exec_module`` because
    ``@dataclass`` resolves annotations through ``sys.modules[__module__]``
    while processing the class.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _make_shadow_root(tmp_path: Path) -> Path:
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    for rel in _SHADOW_RELATIVE_PATHS:
        src = ROOT / rel
        if not src.exists():
            continue
        dst = shadow / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return shadow


def _check(brc, shadow: Path) -> int:
    """Run the writer's ``--check`` against ``shadow``. Never writes."""
    return brc.run(write=False, mode_label="check", rotate_card_hash=False, root=shadow)


def _changed_files(capsys) -> set[str]:
    """Parse the writer's report for the files it says would change."""
    out = capsys.readouterr().out
    return {m.group(1) for m in re.finditer(r"^\s*changed\s+(\S+)", out, flags=re.MULTILINE)}


@pytest.fixture
def brc():
    return _load("brc_count_sources", BUILD_SCRIPT)


@pytest.fixture
def shadow(tmp_path: Path) -> Path:
    return _make_shadow_root(tmp_path)


# ---------------------------------------------------------------------------
# 0. Baseline — the shadow is green before any mutation.
#    Without this, a red result below proves nothing.
# ---------------------------------------------------------------------------


def test_shadow_baseline_is_green(brc, shadow, capsys):
    """An untouched shadow passes ``--check``.

    This is the control for every mutation test in this file: each one
    asserts RED, and RED only means something if the same tree was GREEN a
    moment earlier.
    """
    assert _check(brc, shadow) == 0, f"shadow tree is not green before mutation: {capsys.readouterr().out}"


# ---------------------------------------------------------------------------
# 1. Truth-moves: the language registry
# ---------------------------------------------------------------------------


def test_language_count_tracks_a_moved_registry(brc, shadow, monkeypatch, capsys):
    """Add a language to the registry; ``--check`` must go RED and name README.

    This is the test the old generator could not pass at any effort level:
    it had no ``languages`` field, so the registry could grow without a
    single byte of its output changing. ``_live_languages()`` imports
    ``roam.languages.registry`` at call time precisely so this patch is
    observed by the real reader.
    """
    import roam.languages.registry as registry

    real = registry.get_supported_languages()
    assert _check(brc, shadow) == 0, "precondition: shadow must be green before the registry moves"
    capsys.readouterr()

    monkeypatch.setattr(registry, "get_supported_languages", lambda: [*real, "_moved_truth_lang"])
    # The writer must SEE the moved registry...
    assert brc.collect_counts(shadow).languages == len(real) + 1, (
        "collect_counts ignored the patched registry — the languages field is not reading the real source"
    )
    # ...and the docs, which still say len(real), must now be drift.
    assert _check(brc, shadow) == 1, (
        "registry grew by one language and --check stayed green: the language count is a mirror, not a measurement"
    )
    assert "README.md" in _changed_files(capsys), "--check went red but did not name README.md as the drifted file"


# ---------------------------------------------------------------------------
# 2. Truth-moves: the SARIF consumer tuple
# ---------------------------------------------------------------------------


def _grow_sarif_tuple(shadow: Path) -> int:
    """Append one entry to ``_SARIF_CONSUMERS`` in the SHADOW cli.py.

    Mutates real source text and lets the real AST parser read it, rather
    than stubbing the counting function — otherwise the test would only
    prove that a patched integer propagates, not that the count is derived
    from the tuple ``roam --sarif`` actually dispatches on.
    """
    cli_path = shadow / "src" / "roam" / "cli.py"
    text = cli_path.read_text(encoding="utf-8")
    anchor = "_SARIF_CONSUMERS: tuple[str, ...] = (\n"
    assert anchor in text, "shadow cli.py no longer declares _SARIF_CONSUMERS as a literal tuple"
    cli_path.write_text(text.replace(anchor, anchor + '    "_moved-truth-cmd",\n', 1), encoding="utf-8")
    return 1


def test_sarif_count_tracks_a_moved_consumer_tuple(brc, shadow, monkeypatch, capsys):
    """Grow ``cli._SARIF_CONSUMERS`` by one; ``--check`` must go RED.

    ``surface_counts`` resolves ``cli.py`` through ``_package_file`` (which
    is wheel-safe and therefore points at the INSTALLED package, not at
    ``--root``). We redirect just that one filename at the mutated shadow
    copy so the parse runs over genuinely different source text.
    """
    from roam import surface_counts

    real_package_file = surface_counts._package_file
    before = surface_counts.sarif_consumer_count()
    assert _check(brc, shadow) == 0, "precondition: shadow must be green before the tuple moves"
    capsys.readouterr()

    _grow_sarif_tuple(shadow)

    def _redirect(filename: str) -> Path:
        if filename == "cli.py":
            return shadow / "src" / "roam" / "cli.py"
        return real_package_file(filename)

    monkeypatch.setattr(surface_counts, "_package_file", _redirect)
    assert surface_counts.sarif_consumer_count() == before + 1, (
        "the shadow cli.py mutation was not observed — the redirect or the parse is wrong"
    )
    assert _check(brc, shadow) == 1, (
        "_SARIF_CONSUMERS gained a command and --check stayed green: "
        "the README SARIF count does not answer to the tuple"
    )
    assert "README.md" in _changed_files(capsys), "--check went red but did not name README.md as the drifted file"


# ---------------------------------------------------------------------------
# 3. Injection: the exact numbers that used to pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "delta"),
    [("languages", -2), ("sarif", 1)],
)
def test_wrong_count_injected_into_readme_is_caught_and_named(brc, shadow, capsys, kind, delta):
    """Write a wrong count into README.md; ``--check`` must fail and name it.

    ``delta`` reproduces the historical mutation exactly: 28 -> 26 languages
    and 37 -> 38 SARIF commands. Both passed the whole doc-hygiene job and
    every doc-guard test before the ``languages`` / ``sarif_commands``
    fields existed.
    """
    counts = brc.collect_counts(shadow)
    if kind == "languages":
        truth, wrong = f"{counts.languages} languages", f"{counts.languages + delta} languages"
    else:
        truth, wrong = f"{counts.sarif_commands} commands", f"{counts.sarif_commands + delta} commands"

    readme = shadow / "README.md"
    original = readme.read_text(encoding="utf-8")
    assert truth in original, f"precondition failed: {truth!r} is not in README.md"
    mutated = original.replace(truth, wrong)
    assert mutated != original, "mutation was a no-op"
    readme.write_text(mutated, encoding="utf-8")

    assert _check(brc, shadow) == 1, f"README claiming {wrong!r} (real: {truth!r}) passed --check"
    assert "README.md" in _changed_files(capsys), "--check went red but did not name README.md"

    # And the writer repairs exactly this: restore, and green returns.
    readme.write_text(original, encoding="utf-8")
    assert _check(brc, shadow) == 0, "restoring the true counts did not return the check to green"


# ---------------------------------------------------------------------------
# 4. The literal guard — stop the mirror growing back
# ---------------------------------------------------------------------------


def test_writer_hardcodes_no_language_or_sarif_literal():
    """The generator must not contain a hardcoded language / SARIF count.

    The original defect was not a wrong number; it was a number that no
    reader derived. ``28`` appeared in four f-strings, so ``--check`` could
    only ever confirm the generator agreed with itself. This guard fails if
    a future edit reintroduces a literal instead of using ``Counts``.
    """
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    # Comments are stripped: prose explaining this history is allowed to say
    # "28", only emitted string content is a publishing surface. A literal
    # digit run here can never be a placeholder — ``{c.languages}`` contains
    # no digits — so matching digits is sufficient and needs no extra filter.
    # (The first version of this guard tried to filter on ``split("\\b")``,
    # which in a non-raw string is a BACKSPACE character, not a backslash;
    # it silently excluded every real offender. Caught by running the
    # negative control instead of trusting the green tick.)
    # The NOUN that follows the number, not the topic word.
    #
    # The first version of this guard matched ``\d+ (?:languages|SARIF)``, which
    # cannot fire for SARIF: that prose reads "{c.sarif_commands} commands
    # honour ...", so a hardcoded regression says "37 commands" and the digits
    # are never followed by "SARIF". Measured: re.findall on the emitted text
    # returned [] for the SARIF half against a deliberately hardcoded 37. Half
    # this guard was decorative -- the same vacuous-check defect the file exists
    # to prevent, inside the file that prevents it. Found by an adversarial
    # re-run, not by the green tick.
    #
    # Matching the noun also keeps "SARIF 2.1.0" out: that digit run is a spec
    # version followed by a comma, not a count followed by a unit. Verified:
    # the pattern returns [] on the version prose and non-empty on both
    # hardcoded forms.
    emitted = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    offenders = [m.group(0) for m in re.finditer(r'f"[^"]*?\b\d+ (?:languages|commands|MCP tools)\b', emitted)]
    assert not offenders, (
        f"dev/build_readme_counts.py emits a hardcoded count literal: {offenders}. "
        "Counts.languages / Counts.sarif_commands exist so the prose answers to the registry "
        "and to cli._SARIF_CONSUMERS — a literal makes --check a mirror again."
    )
    assert "languages: int" in text, "Counts lost its languages field"
    assert "sarif_commands: int" in text, "Counts lost its sarif_commands field"


# ---------------------------------------------------------------------------
# 5. AGENTS.md command-module census
# ---------------------------------------------------------------------------


def test_agents_md_module_census_answers_to_the_real_census():
    """AGENTS.md's ``N command modules: M back the K default names`` is derived.

    It stated 274 / 272 / 281 while the file's own marker-protected headline
    said 285 commands — three numbers in one file, none of them read by
    anything. The sync script now builds that sentence from
    ``surface_counts.command_module_counts()`` and ``cli._COMMANDS``.
    """
    from roam.surface_counts import cli_surface_counts, command_module_counts

    census = command_module_counts()
    commands = int(cli_surface_counts()["command_names"])
    expected = (
        f"# {census['modules']} command modules: "
        f"{census['backing_default_names']} back the {commands} default names; "
        f"{census['feature_gated']} are feature-gated"
    )
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert expected in agents, (
        f"AGENTS.md's command-module census disagrees with the real census.\nExpected: {expected!r}\n"
        "Run: python scripts/sync_surface_counts.py --write"
    )
    assert f'toward the "{commands} commands" headline' in agents, (
        f"AGENTS.md quotes a different headline than its own auto-count headline ({commands}). "
        "Run: python scripts/sync_surface_counts.py --write"
    )


def test_module_census_is_internally_consistent():
    """``modules == backing + feature_gated`` — the sentence cannot lie by arithmetic."""
    from roam.surface_counts import command_module_counts

    census = command_module_counts()
    assert census["modules"] == census["backing_default_names"] + census["feature_gated"]
    assert census["modules"] > 0
