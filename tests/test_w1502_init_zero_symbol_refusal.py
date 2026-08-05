"""W1502 — ``roam init`` must not report a broken index as a success.

The defect: ``roam init`` printed ``Roam is ready: N files, 0 symbols,
0 edges`` and exited 0 for at least six structurally different situations,
four of which are outright broken. An agent that reads that transcript has
no way to tell "this repo has no code in it" from "the indexer understood
nothing, and every symbol-, call-graph- and dependency-based command will
now return a vacuously clean answer". This already produced one wrong
conclusion on a measurement run against a venv that had indexed 0 rows.

The contract this file pins is three-valued, not two-valued:

  a) indexing FAILED (files in a supported language yielded zero symbols)
     -> non-zero exit, and the output NAMES the cause.
  b) legitimately EMPTY (docs-only repo, everything excluded, empty tree)
     -> exit 0, but the output SAYS there is no indexable content and why,
        and is distinguishable from (a).
  c) a normal repo -> unaffected, exit 0, counts stated.

The distinction is carried by ``roam.index.corpus_state.classify`` so that
``init``, ``index`` and ``doctor`` share one boundary instead of each
re-deriving "0 symbols means...".
"""

from __future__ import annotations

import json

from tests.conftest import git_init, roam


def _write(path, name, body):
    p = path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# (a) BROKEN ZERO — must refuse
# ---------------------------------------------------------------------------


def test_init_refuses_when_supported_files_yield_no_symbols(tmp_path):
    """Every .py file fails to parse -> refusal, not 'Roam is ready'."""
    _write(tmp_path, "broken1.py", "def (((( ###\n  !!!! unbalanced\n")
    _write(tmp_path, "broken2.py", ")))) ????\n")
    git_init(tmp_path)

    out, code = roam("init", "--yes", cwd=tmp_path)

    assert code != 0, f"broken index exited 0 and reported success:\n{out}"
    assert "indexing failed" in out.lower(), f"refusal does not name the failure:\n{out}"
    assert "0 symbols" in out or "zero symbols" in out.lower(), out
    assert "Roam is ready" not in out, f"success banner printed for a broken index:\n{out}"


def test_init_json_refusal_carries_machine_readable_state(tmp_path):
    """JSON consumers get state/reason, not just a prose message."""
    _write(tmp_path, "broken.py", "def (((( ###\n")
    git_init(tmp_path)

    out, code = roam("--json", "init", "--yes", cwd=tmp_path)

    assert code != 0, f"broken index exited 0 in JSON mode:\n{out}"
    payload = json.loads(out[out.index("{") : out.rindex("}") + 1])
    summary = payload["summary"]
    assert summary["state"] == "indexing_failed", summary
    assert summary["reason"] == "parsers_extracted_nothing", summary
    assert summary.get("partial_success") is True, summary


def test_index_command_shares_the_same_refusal(tmp_path):
    """``roam index`` inherits the boundary — not just ``roam init``."""
    _write(tmp_path, "broken.py", "def (((( ###\n")
    git_init(tmp_path)

    out, code = roam("index", cwd=tmp_path)

    assert code != 0, f"`roam index` exited 0 on a zero-symbol index:\n{out}"
    assert "indexing failed" in out.lower(), out


# ---------------------------------------------------------------------------
# (b) LEGITIMATE ZERO — must still exit 0, and must say why
# ---------------------------------------------------------------------------


def test_init_docs_only_repo_succeeds_but_discloses_no_content(tmp_path):
    """A docs repo is a legitimate zero: exit 0, but say there is no source."""
    _write(tmp_path, "README.md", "# hello\n")
    _write(tmp_path, "notes.txt", "some notes\n")
    git_init(tmp_path)

    out, code = roam("init", "--yes", cwd=tmp_path)

    assert code == 0, f"legitimate docs-only repo was failed:\n{out}"
    assert "no indexable content" in out.lower(), f"empty zero not disclosed:\n{out}"
    assert "indexing failed" not in out.lower(), f"legitimate zero reported as failure:\n{out}"


def test_init_empty_tree_succeeds_and_names_the_empty_tree(tmp_path):
    git_init(tmp_path)

    out, code = roam("init", "--yes", cwd=tmp_path)

    assert code == 0, out
    assert "no indexable content" in out.lower(), out
    assert "indexing failed" not in out.lower(), out


def test_init_all_source_excluded_names_the_filter(tmp_path):
    """The .roamignore-swallowed-src case: exit 0, but name what dropped it."""
    _write(tmp_path, "src/mod.py", "def alpha():\n    return 1\n")
    _write(tmp_path, ".roamignore", "src/\n")
    git_init(tmp_path)

    out, code = roam("--json", "init", "--yes", cwd=tmp_path)

    assert code == 0, out
    payload = json.loads(out[out.index("{") : out.rindex("}") + 1])
    summary = payload["summary"]
    assert summary["state"] == "no_indexable_content", summary
    assert summary["reason"] == "all_source_filtered", summary
    corpus = payload["corpus"]
    assert corpus["candidates_on_disk"] >= 1, corpus
    assert corpus["filtered_by"], "filter attribution missing — cause is still unnamed"


def test_init_all_source_gitignored_names_the_filter(tmp_path):
    """Source under a .gitignore'd directory is invisible to git ls-files."""
    _write(tmp_path, "app/mod.py", "def alpha():\n    return 1\n")
    _write(tmp_path, ".gitignore", "app/\n")
    git_init(tmp_path)

    out, code = roam("--json", "init", "--yes", cwd=tmp_path)

    assert code == 0, out
    payload = json.loads(out[out.index("{") : out.rindex("}") + 1])
    assert payload["summary"]["state"] == "no_indexable_content", payload["summary"]
    assert "untracked_or_gitignored" in payload["corpus"]["filtered_by"], payload["corpus"]


def test_init_oversized_source_is_not_reported_as_an_empty_tree(tmp_path):
    """A file dropped by the 1MB cap must not read like an empty directory."""
    big = "def alpha():\n    return 1\n" + ("# pad\n" * 300_000)
    _write(tmp_path, "big.py", big)
    git_init(tmp_path)

    out, code = roam("--json", "init", "--yes", cwd=tmp_path)

    assert code == 0, out
    payload = json.loads(out[out.index("{") : out.rindex("}") + 1])
    corpus = payload["corpus"]
    assert payload["summary"]["reason"] != "empty_tree", (
        "a repo whose only source file exceeded the size cap was reported as an empty tree"
    )
    assert "over_size_cap" in corpus["filtered_by"], corpus


# ---------------------------------------------------------------------------
# (c) NORMAL REPO — unaffected
# ---------------------------------------------------------------------------


def test_init_normal_repo_still_succeeds_with_counts(tmp_path):
    _write(tmp_path, "mod.py", "def alpha():\n    return beta()\n\n\ndef beta():\n    return 1\n")
    git_init(tmp_path)

    out, code = roam("init", "--yes", cwd=tmp_path)

    assert code == 0, out
    assert "Roam is ready" in out, out
    assert "no indexable content" not in out.lower(), out
    assert "indexing failed" not in out.lower(), out


def test_init_normal_repo_json_state_is_indexed(tmp_path):
    _write(tmp_path, "mod.py", "def alpha():\n    return 1\n")
    git_init(tmp_path)

    out, code = roam("--json", "init", "--yes", cwd=tmp_path)

    assert code == 0, out
    payload = json.loads(out[out.index("{") : out.rindex("}") + 1])
    assert payload["summary"]["state"] == "indexed", payload["summary"]
    assert payload["corpus"]["symbols"] > 0, payload["corpus"]


# ---------------------------------------------------------------------------
# doctor shares the vocabulary
# ---------------------------------------------------------------------------


def test_doctor_distinguishes_broken_zero_from_legitimate_zero(tmp_path):
    """`roam doctor` must not call both zeros the same thing."""
    _write(tmp_path, "broken.py", "def (((( ###\n")
    git_init(tmp_path)
    roam("index", cwd=tmp_path)

    broken_out, _ = roam("doctor", cwd=tmp_path)

    docs = tmp_path.parent / (tmp_path.name + "_docs")
    docs.mkdir()
    _write(docs, "README.md", "# hi\n")
    git_init(docs)
    roam("index", cwd=docs)

    docs_out, _ = roam("doctor", cwd=docs)

    assert "indexing_failed" in broken_out or "indexing failed" in broken_out.lower(), broken_out
    assert "no_indexable_content" in docs_out or "no indexable content" in docs_out.lower(), docs_out
    assert broken_out != docs_out, "doctor reports both zeros identically"
