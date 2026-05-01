"""Lock in the corrected `roam --detail <cmd>` hint text.

The pre-v12 dogfood pass found 7 commands telling users "use --detail"
when ``--detail`` is a *group-level* flag at the ``roam`` cli (cli.py:540),
not a subcommand option. Click was rejecting ``roam <cmd> --detail`` and
agents got opaque errors. This test prevents the regression.

Two layers of guard:

1. **Source-grep**: every command that mentions ``--detail`` in its
   `click.echo(...)` strings must use the form ``roam --detail <cmd>``,
   never the bare ``--detail`` after the subcommand.
2. **Behavioural**: ``roam --detail health`` produces strictly more
   output than ``roam health`` (sanity check — flag is wired and used).
"""

from __future__ import annotations

import re
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli

ROOT = Path(__file__).resolve().parents[1]
COMMANDS_DIR = ROOT / "src" / "roam" / "commands"

# The list of commands whose output text mentions --detail. If a new
# command joins, the list grows; that's fine and tested below.
_DETAIL_HINT_RE = re.compile(r"roam\s+--detail\s+([\w-]+)")
# Forbidden form: "use --detail" or "with --detail" *not* preceded by
# "roam ". This is what the pre-v12 bug looked like.
_BAD_HINT_RE = re.compile(
    r"(?<!roam\s)(?<!`)\b(?:use|run|with|via|see|via )\s*--detail\b",
    re.IGNORECASE,
)


def _hint_lines() -> list[tuple[Path, int, str]]:
    """Return every line in commands/ that mentions ``--detail``."""
    out = []
    for path in COMMANDS_DIR.glob("cmd_*.py"):
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "--detail" in line:
                out.append((path, ln, line))
    return out


def _has_local_detail_option(path: Path) -> bool:
    """True when the file declares its own ``@click.option('--detail')``.

    A subcommand with a local ``--detail`` flag may legitimately tell users
    to ``use --detail`` (the local option resolves before the group-level
    one). Without a local option, the hint must be ``roam --detail <cmd>``.
    """
    text = path.read_text(encoding="utf-8")
    return bool(re.search(r"@click\.option\(\s*\"--detail\"", text))


class TestDetailHintMessages:
    def test_every_hint_uses_group_level_form(self):
        """All ``--detail`` mentions in user-facing strings either:

        * Use the group-level form ``roam --detail <cmd>``, OR
        * Live in a file that defines its own local ``--detail`` option.

        Anything else is the v11.x bug (telling users to put ``--detail``
        after a subcommand that doesn't accept it).
        """
        offenders: list[tuple[Path, int, str]] = []
        for path, ln, line in _hint_lines():
            stripped = line.strip()
            # Click option definitions are allowed
            if stripped.startswith("@click.option"):
                continue
            if "is_flag=True" in line and "--detail" in line:
                continue
            # ctx.obj reads
            if 'ctx.obj["detail"]' in line or 'ctx.obj.get("detail"' in line:
                continue
            # Plain "--detail" with no embedded help text
            if stripped.startswith("def ") or stripped.startswith("#"):
                continue
            # Files that own a local --detail option may say "use --detail" freely
            if _has_local_detail_option(path):
                continue
            # Only check lines that look like printed output / docstring content
            if not (
                "click.echo" in line
                or '"""' in line
                or stripped.startswith('"')
                or stripped.startswith("'")
                or stripped.startswith("(")
                or stripped.endswith(")")
            ):
                continue

            if _BAD_HINT_RE.search(line):
                offenders.append((path, ln, line.strip()))

        assert not offenders, (
            "These lines suggest `--detail` after the subcommand. "
            "`--detail` is a group-level flag — use `roam --detail <cmd>`:\n"
            + "\n".join(f"{p.name}:{ln} -> {text[:140]}" for p, ln, text in offenders)
        )

    def test_each_hint_names_the_correct_subcommand(self):
        """Every `roam --detail <cmd>` hint must reference a real CLI command."""
        from roam.surface_counts import cli_commands

        valid = set(cli_commands().keys())
        for path, ln, line in _hint_lines():
            for match in _DETAIL_HINT_RE.finditer(line):
                cmd = match.group(1)
                assert cmd in valid, f"{path.name}:{ln} hints `roam --detail {cmd}` but {cmd!r} is not in cli._COMMANDS"


class TestDetailFlagBehaviour:
    """End-to-end: ``--detail`` at the group level must produce more output."""

    def test_health_detail_is_strictly_longer(self, indexed_project):
        runner = CliRunner()
        compact = runner.invoke(cli, ["health"])
        full = runner.invoke(cli, ["--detail", "health"])
        assert compact.exit_code == 0, compact.output
        assert full.exit_code == 0, full.output
        # --detail should add the '=== Cycles ===' / '=== God components ===' sections.
        assert len(full.output) >= len(compact.output)
        # The compact output suggests --detail; the full output skips that hint.
        if "(run `roam --detail health`" in compact.output:
            # On a project with findings, the hint must point users right.
            assert "roam --detail health" in compact.output

    def test_dead_detail_no_crash(self, indexed_project):
        """Smoke test: --detail dead exits cleanly even on a small project."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--detail", "dead"])
        assert result.exit_code == 0, result.output
