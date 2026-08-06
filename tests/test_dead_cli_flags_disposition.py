"""Per-flag behaviour pins for the 2026-08-06 dead-CLI-flag sweep.

``scripts/audit_dead_cli_flags.py`` found six click params that were declared,
accepted from the user, and then never read by their command body. The auditor
proves they are *referenced* now; this file proves each one does the thing its
disposition claims, because "the name appears in the body" and "the flag works"
are different assertions and only the second is what a user experiences.

Dispositions pinned here:

* ``roam file --full``      WIRED — actually lifts the imports/importers cap.
* ``roam verify --changed`` HONOURED — no-op alone (it names the default),
  rejected when combined with explicit FILES, which is the case that silently
  mis-scoped a gate.
* ``roam budget --staged``  DELETED — unimplementable against whole-tree metric
  snapshots; it scored the whole tree while claiming to be scoped.
* ``roam api-changes --changed`` DELETED — the narrowing it promised is already
  unconditional, so it could only be a no-op or a silent gate-weakening.
* ``roam compile --model-tier``  DELETED — no model-tier concept exists in the
  artifact selector; all three values produced byte-identical output.
* ``roam init --yes``       ACCEPTED NO-OP — init never prompts; kept for
  callers, allowlisted with a reason.
"""

from __future__ import annotations

from tests.conftest import git_init, roam

# ---------------------------------------------------------------------------
# WIRED: roam file --full
# ---------------------------------------------------------------------------


def _project_with_many_imports(tmp_path, n=12):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for i in range(n):
        (pkg / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    body = "".join(f"from pkg.m{i} import f{i}\n" for i in range(n))
    body += "\n\ndef main():\n    return " + " + ".join(f"f{i}()" for i in range(n)) + "\n"
    (tmp_path / "main.py").write_text(body, encoding="utf-8")
    git_init(tmp_path)
    out, code = roam("init", "--yes", cwd=tmp_path)
    assert code == 0, out
    return tmp_path


def test_file_full_lifts_the_import_truncation(tmp_path):
    """Without --full the deps line truncates with (+N); with it, everything shows.

    Pre-fix this pair was byte-identical: `full` was declared at the decorator,
    named in the signature, advertised in the docstring example, and read
    nowhere — 23 of 31 imports stayed hidden in both runs.
    """
    proj = _project_with_many_imports(tmp_path)

    plain, rc1 = roam("file", "main.py", cwd=proj)
    full, rc2 = roam("file", "main.py", "--full", cwd=proj)
    assert rc1 == 0, plain
    assert rc2 == 0, full

    assert plain != full, "--full produced byte-identical output — the flag is dead again"

    dep_lines = [ln for ln in plain.splitlines() if ln.startswith(("imports (", "importers ("))]
    assert dep_lines, f"no dependency line to truncate in:\n{plain}"
    assert any("(+" in ln for ln in dep_lines), f"expected a (+N) truncation marker in:\n{plain}"

    full_dep_lines = [ln for ln in full.splitlines() if ln.startswith(("imports (", "importers ("))]
    assert full_dep_lines, f"no dependency line in --full output:\n{full}"
    assert not any("(+" in ln for ln in full_dep_lines), f"--full still truncated:\n{full}"

    # The count in the header must match what is actually listed.
    for line in full_dep_lines:
        declared = int(line.split("(", 1)[1].split(")", 1)[0])
        listed = len([p for p in line.split(":", 1)[1].split(",") if p.strip()])
        assert listed == declared, f"--full listed {listed} of {declared}: {line}"


# ---------------------------------------------------------------------------
# HONOURED: roam verify --changed
# ---------------------------------------------------------------------------


def test_verify_rejects_changed_combined_with_explicit_files(tmp_path):
    """`roam verify FILE --changed` asks for two different scopes on a GATE.

    Pre-fix the flag was silently dropped and the gate scoped to FILE, so an
    operator who asked for the git diff got a narrower verdict with no notice.
    There is no safe silent winner here, so refuse.
    """
    (tmp_path / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    git_init(tmp_path)

    out, code = roam("verify", "a.py", "--changed", cwd=tmp_path)
    assert code == 2, f"expected a usage error, got {code}:\n{out}"
    assert "INVALID_OPTIONS" in out, out
    assert "--changed" in out, out


def test_verify_changed_alone_is_still_accepted(tmp_path):
    """With no FILES the flag agrees with the documented default — not an error."""
    (tmp_path / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    git_init(tmp_path)
    out, _ = roam("init", "--yes", cwd=tmp_path)

    out, code = roam("verify", "--changed", cwd=tmp_path)
    assert code != 2, f"`verify --changed` alone must not be a usage error:\n{out}"
    assert "INVALID_OPTIONS" not in out, out


# ---------------------------------------------------------------------------
# DELETED: flags that cannot be honoured
# ---------------------------------------------------------------------------


def _rejects(argv, tmp_path):
    out, code = roam(*argv, cwd=tmp_path)
    assert code == 2, f"expected exit 2 (click usage error) for {argv}, got {code}:\n{out}"
    assert "no such option" in out.lower(), out
    return out


def test_budget_staged_is_gone(tmp_path):
    """budget compares WHOLE-TREE metric snapshots; there is no staged scope.

    Accepting the flag and scoring the whole tree anyway is a gate lying about
    its own scope, which is worse than not offering the mode.
    """
    _rejects(["budget", "--staged"], tmp_path)


def test_api_changes_changed_is_gone(tmp_path):
    """The command's only file source is already `git diff --name-only <base>`.

    So the flag could be a no-op (what it was) or, if re-scoped to the
    uncommitted diff, could silently SHRINK a `--severity breaking` gate.
    """
    _rejects(["api-changes", "--changed"], tmp_path)


def test_compile_model_tier_is_gone(tmp_path):
    """No model-tier concept exists in the artifact selector.

    ``select_artifact`` keys only on procedure + classifier_confidence, and the
    flag's help asserted a dated empirical result for a knob that changed
    nothing. Use ``--artifact`` to pick an envelope shape.
    """
    _rejects(["compile", "some task text here", "--model-tier", "capable"], tmp_path)


def test_compile_model_tier_claim_is_gone_from_every_string_literal():
    """The dated empirical claim must not outlive the knob it justified.

    ``--model-tier``'s help asserted "Empirically locked 2026-05-28: facts
    dominates on Opus 4.8" for a flag measured to produce byte-identical output
    across all three choices. A claim with no mechanism behind it is worse than
    no claim: a reader budgets trust against it.
    """
    import ast
    from pathlib import Path

    from tests._helpers.repo_root import repo_root

    src = Path(repo_root(), "src", "roam", "commands", "cmd_compile.py").read_text(encoding="utf-8")
    literals = [
        node.value
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    joined = "\n".join(literals)
    assert "--model-tier" not in joined, "--model-tier is a live click option / help string again"
    assert "Empirically locked 2026-05-28" not in joined


# ---------------------------------------------------------------------------
# ACCEPTED NO-OP: roam init --yes
# ---------------------------------------------------------------------------


def test_init_yes_is_still_accepted(tmp_path):
    """Allowlisted, not deleted: scripts, CI recipes and the MCP wrapper pass it."""
    (tmp_path / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    git_init(tmp_path)

    out, code = roam("init", "--yes", cwd=tmp_path)
    assert code == 0, out


def test_init_help_no_longer_claims_to_be_interactive():
    """init makes no prompting CALL, so it cannot have an interactive mode.

    Checked on the AST, not the raw text: the module's own comments explain the
    allowlist entry by naming ``click.confirm`` / ``click.prompt`` / ``input``,
    and a substring scan would trip over the explanation instead of the code.
    """
    import ast
    from pathlib import Path

    from tests._helpers.repo_root import repo_root

    src = Path(repo_root(), "src", "roam", "commands", "cmd_init.py").read_text(encoding="utf-8")
    called = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            called.add(fn.attr)
        elif isinstance(fn, ast.Name):
            called.add(fn.id)
    prompting = called & {"confirm", "prompt", "input"}
    assert not prompting, (
        f"init grew a prompt ({sorted(prompting)}) — --yes now has real work to do; "
        "wire it and drop the scripts/dead_cli_flags_allowlist.txt entry"
    )

    literals = "\n".join(
        node.value
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    assert "interactive bootstrap" not in literals
