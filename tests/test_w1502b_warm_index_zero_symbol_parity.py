"""W1502-followup — the zero-symbol verdict must not depend on WHO ran it.

W1502 gave ``init`` / ``index`` / ``doctor`` a three-valued verdict over a
zero-symbol corpus, and the discriminator between "legitimately empty" and
"broken" is the parser's error count. That count is per-PROCESS module state
scoped to the files THAT run parsed, and the number carries no record of which
files those were — so a caller that parsed nothing hands the classifier a zero
that reads exactly like "every file parsed cleanly".

Measured 2026-08-12 on a one-file repo whose only ``.py`` is unparseable:

    roam init          -> exit 1  "indexing failed: 1 file(s) ... zero symbols"
    roam init  (again) -> exit 0  "no indexable content: 1 file(s) ... parsed
                                   without error"
    roam index         -> exit 0  (same false clean; the run re-parsed only the
                                   .roamignore that init had just written)
    roam index --force -> exit 1

Same index, same bytes on disk, opposite verdicts — and the exit-0 branch is
the one a CI job or an agent hits on every run after the first. That is the
estate's signature defect: an ABSENT measurement rendered as a definite
success value.

The fix re-measures the corpus's own files at the verdict boundary
(``corpus_state._probe_parse_errors``) instead of trusting a scope-blind
count. These tests pin BOTH directions: the broken zero must keep refusing on
a warm run, and the legitimate zero must keep exiting 0 on a warm run. A fix
that refuses everything passes the first half and fails the second.
"""

from __future__ import annotations

import json

from tests.conftest import git_init, roam

# Unparseable in any grammar roam ships. tree-sitter does not raise on this —
# it returns a tree full of ERROR nodes — so "the parser did not blow up" is
# not evidence the file was understood.
_BROKEN_PY = "def (((( ###\n  !!!! unbalanced\n"


def _write(path, name, body):
    p = path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _summary(out):
    return json.loads(out[out.index("{") : out.rindex("}") + 1])["summary"]


def test_second_init_repeats_the_refusal_on_an_unchanged_index(tmp_path):
    """The defect, end to end: init twice, nothing changes, verdict flips."""
    _write(tmp_path, "broken.py", _BROKEN_PY)
    git_init(tmp_path)

    first_out, first_code = roam("--json", "init", "--yes", cwd=tmp_path)
    second_out, second_code = roam("--json", "init", "--yes", cwd=tmp_path)

    assert first_code != 0, f"cold init did not refuse the broken index:\n{first_out}"
    assert second_code != 0, (
        "a second `roam init` over the SAME broken index exited 0 — the refusal "
        f"only holds when the process happens to have done the parsing:\n{second_out}"
    )
    assert _summary(second_out)["state"] == "indexing_failed", _summary(second_out)
    assert "parsed without error" not in second_out, (
        f"warm init claimed a parse result it never measured:\n{second_out}"
    )


def test_warm_index_repeats_the_refusal(tmp_path):
    """``roam index`` without ``--force`` re-parses only what changed.

    On an unchanged corpus that is zero source files, so its parse-error count
    is a measurement of nothing. It must not outvote what the index holds.
    """
    _write(tmp_path, "broken.py", _BROKEN_PY)
    git_init(tmp_path)

    roam("init", "--yes", cwd=tmp_path)
    warm_out, warm_code = roam("index", cwd=tmp_path)
    forced_out, forced_code = roam("index", "--force", cwd=tmp_path)

    assert forced_code != 0, f"`roam index --force` did not refuse the broken index:\n{forced_out}"
    assert warm_code != 0, (
        "`roam index` exited 0 on the same index `--force` refuses — the verdict "
        f"tracked which files that run re-parsed, not what the index holds:\n{warm_out}"
    )


def test_classify_ignores_a_scope_blind_zero_from_the_caller(tmp_path):
    """The boundary itself, with the caller's hint set to the worst case.

    ``parse_errors=0`` is what every warm caller passes: truthful about the
    process, silent about the corpus. The classifier must re-measure rather
    than treat it as a clean bill of health for files it never parsed.
    """
    from roam.db.connection import open_db
    from roam.index.corpus_state import STATE_FAILED, classify

    _write(tmp_path, "broken.py", _BROKEN_PY)
    git_init(tmp_path)
    roam("index", cwd=tmp_path)

    with open_db(readonly=True, project_root=tmp_path) as conn:
        verdict = classify(conn, tmp_path, parse_errors=0)

    assert verdict.state == STATE_FAILED, verdict
    assert verdict.ok is False, verdict


def test_second_init_on_an_import_only_tree_stays_a_legitimate_zero(tmp_path):
    """Conservation control: the fix must not refuse the OTHER zero.

    An empty ``__init__.py`` beside an import-only module parses perfectly and
    declares nothing. Refusing it on the second run would swap one conflation
    for its mirror image — every warm run of a docs-shaped repo failing CI.
    """
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/consumer.py", "import os\nimport sys\n")
    git_init(tmp_path)

    first_out, first_code = roam("--json", "init", "--yes", cwd=tmp_path)
    second_out, second_code = roam("--json", "init", "--yes", cwd=tmp_path)

    assert first_code == 0, f"cold init refused a clean import-only tree:\n{first_out}"
    assert second_code == 0, f"warm init refused a clean import-only tree:\n{second_out}"
    assert _summary(second_out)["state"] == "no_indexable_content", _summary(second_out)
    assert _summary(second_out)["reason"] == "files_define_no_symbols", _summary(second_out)


def test_second_init_on_a_normal_repo_is_untouched(tmp_path):
    """A repo with symbols never reaches the zero-symbol path at all."""
    _write(tmp_path, "mod.py", "def alpha():\n    return beta()\n\n\ndef beta():\n    return 1\n")
    git_init(tmp_path)

    roam("init", "--yes", cwd=tmp_path)
    out, code = roam("--json", "init", "--yes", cwd=tmp_path)

    assert code == 0, out
    summary = _summary(out)
    assert summary["state"] == "indexed", summary
