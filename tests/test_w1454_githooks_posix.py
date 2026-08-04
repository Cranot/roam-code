"""W1454 — a `#!/bin/sh` hook must not use bash-only syntax.

`.githooks/pre-commit` contained `${STAGED_PY// /}` — a BASH pattern
substitution — under a `#!/bin/sh` shebang. On dash, which is `/bin/sh` on
Debian, Ubuntu, and effectively every Linux CI runner and build lane this
project uses, that fails and the hook exits 2, so every commit is refused:

    $ dash -c 'V="a.py "; if [ -n "${V// /}" ]; then echo RUNS; fi'
    dash: 1: Bad substitution

MEASURED, because the first version of this docstring got it wrong twice.

It is NOT a parse error: `dash -n` on the broken hook exits 0. dash parses
the construct happily and fails at EXPANSION time, when control reaches the
line. So it does NOT abort "before any check runs" — every gate ABOVE it
executes and prints its normal success output, and only that gate and the
ones below are skipped. That is what made it hard to read: the hook looks
like it is working right up until it dies.

Both errors pointed the same way — they made the failure sound earlier and
more total than it was — which is worth noticing, because a wrong mechanism
that still predicts "commits fail" survives exactly as long as nobody needs
the mechanism for anything.

It survived because of WHERE it was written. On Windows, git-bash makes
`/bin/sh` bash, so the construct works perfectly on the author's machine and
nowhere else. And a parse check cannot catch it on ANY host — `sh -n` exits 0
under dash (it is not a parse error) and invokes bash here (which has the
construct). Only real execution under dash, or a static scan, detects it. So
the guard is a STATIC scan for constructs `sh` does not have, which is what
this file does.

That is the general lesson worth pinning: a portability bug cannot be caught
by executing on the platform that lacks the problem.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers.repo_root import repo_root  # noqa: E402

# W572/W588 — ask git for the canonical toplevel rather than walking
# `Path(__file__).parents[n]`, which lands on the worktree root under nested
# dispatch and would make this suite scan a `.githooks/` that isn't there.
HOOKS_DIR = repo_root() / ".githooks"

# Constructs bash has and POSIX sh does not. Each entry is (regex, what it is).
# Deliberately narrow: a false positive here blocks every commit, so only
# shapes that are unambiguously bash-only are listed.
BASH_ONLY = [
    (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(//|/#|/%)"), "${var//...} pattern substitution"),
    (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\^\^"), "${var^^} case conversion"),
    (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*,,"), "${var,,} case conversion"),
    (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:\d"), "${var:offset} substring"),
    (re.compile(r"\$\{![A-Za-z_]"), "${!var} indirect expansion"),
    (re.compile(r"(?<![<>])\[\["), "[[ ]] test"),
    (re.compile(r"^\s*(local|declare|typeset)\s+-[aAilnrtux]", re.M), "declare/local with flags"),
    (re.compile(r"=~"), "=~ regex match"),
    (re.compile(r"\$\(\([^)]*\+\+"), "C-style ++ in arithmetic"),
    (re.compile(r"^\s*function\s+\w+", re.M), "`function` keyword"),
    (re.compile(r"<<<"), "<<< herestring"),
    (re.compile(r"\|&"), "|& pipe-stderr"),
    (re.compile(r"^\s*mapfile\b|^\s*readarray\b", re.M), "mapfile/readarray"),
]


def _hook_files() -> list[Path]:
    if not HOOKS_DIR.is_dir():
        return []
    return sorted(p for p in HOOKS_DIR.iterdir() if p.is_file() and not p.name.endswith(".sample"))


def _shebang(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[0] if path.stat().st_size else ""


def _strip_noise(text: str) -> str:
    """Drop comments and single-quoted spans before scanning.

    A comment explaining the bash-ism (this fix ships one) must not trip the
    scanner, and neither must a literal inside single quotes, which sh never
    expands.
    """
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        line = re.sub(r"'[^']*'", "''", line)
        out.append(line)
    return "\n".join(out)


def test_githooks_directory_exists() -> None:
    """Guard the guard — if the hooks move, this suite must not pass vacuously."""
    assert HOOKS_DIR.is_dir(), f"{HOOKS_DIR} missing; this suite would silently test nothing"
    assert _hook_files(), "no hook files found; this suite would silently test nothing"


@pytest.mark.parametrize("hook", _hook_files(), ids=lambda p: p.name)
def test_sh_hooks_contain_no_bash_only_syntax(hook: Path) -> None:
    """A `#!/bin/sh` script must RUN under a real POSIX shell.

    Not "parse": dash parses these constructs and fails when it expands
    them, so a parse check passes on a hook that cannot run.
    """
    shebang = _shebang(hook)
    if "bash" in shebang:
        pytest.skip(f"{hook.name} declares bash explicitly: {shebang}")
    if not shebang.startswith("#!"):
        pytest.skip(f"{hook.name} has no shebang")

    body = _strip_noise(hook.read_text(encoding="utf-8", errors="replace"))
    hits = [(m.group(0), what) for pat, what in BASH_ONLY for m in [pat.search(body)] if m]
    assert not hits, (
        f"{hook.name} declares `{shebang}` but uses bash-only syntax: "
        + "; ".join(f"{frag!r} ({what})" for frag, what in hits)
        + ". On dash (/bin/sh on Debian/Ubuntu and our Linux lanes) this fails at "
        "EXPANSION time -- dash parses it, then dies with `Bad substitution` when "
        "control reaches the line, so the gates above it run and print normally and "
        "everything from there down is skipped. `sh -n` will NOT catch this."
    )


def test_the_scanner_actually_detects_the_original_defect() -> None:
    """NEGATIVE CONTROL — prove the scanner can fail.

    Without this, a scanner whose regexes matched nothing would pass every
    test above and report the codebase clean. This feeds it the exact line
    that shipped and requires a hit.
    """
    original = 'STAGED_PY="$(git diff --cached --name-only)"\nif [ -n "${STAGED_PY// /}" ]; then\n'
    body = _strip_noise(original)
    hits = [what for pat, what in BASH_ONLY if pat.search(body)]
    assert hits, "the scanner failed to detect the very construct it was written for"


def test_the_scanner_does_not_flag_the_posix_replacement() -> None:
    """NEGATIVE CONTROL the other way — a scanner that flags everything is useless."""
    fixed = 'STAGED_PY="$(git diff --cached --name-only)"\nif [ -n "$(echo $STAGED_PY)" ]; then\n'
    body = _strip_noise(fixed)
    hits = [what for pat, what in BASH_ONLY if pat.search(body)]
    assert not hits, f"the POSIX form was wrongly flagged as bash-only: {hits}"
