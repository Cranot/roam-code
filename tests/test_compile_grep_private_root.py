"""W196 safety — the grep-replication probe must never pick a private/forbidden
directory (e.g. `internal/`) as its search root. A private named path in the
task otherwise leaks snippets from gitignored files into the compile envelope.
"""

from __future__ import annotations

import os

from roam.plan import compiler


def test_private_dir_names_derived_from_forbidden_defaults():
    # The reject-set must stay in sync with the forbidden-path list.
    names = compiler._PRIVATE_DIR_NAMES
    assert "internal" in names
    assert ".git" in names
    assert ".roam" in names
    assert "node_modules" in names
    # Glob-only / nested entries (e.g. `**/lockfiles/**`) collapse to the name.
    assert "lockfiles" in names
    # Plain files (no `/**`) are not directory names.
    assert "package.json" not in names


def test_is_private_search_root_rejects_internal(tmp_path):
    cwd = str(tmp_path)
    private = tmp_path / "internal" / "planning"
    private.mkdir(parents=True)
    assert compiler._is_private_search_root(str(private), cwd) is True


def test_is_private_search_root_allows_public_dir(tmp_path):
    cwd = str(tmp_path)
    public = tmp_path / "src" / "roam"
    public.mkdir(parents=True)
    assert compiler._is_private_search_root(str(public), cwd) is False


def test_is_private_search_root_rejects_escape(tmp_path):
    # A directory outside the repo root must be refused, not grepped.
    cwd = str(tmp_path / "repo")
    os.makedirs(cwd)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert compiler._is_private_search_root(str(outside), cwd) is True


def test_probe_grep_does_not_use_private_named_path(tmp_path, monkeypatch):
    """A private named path must NOT become the grep search root — it should
    fall back to the repo root (cwd)."""
    cwd = str(tmp_path)
    (tmp_path / "internal" / "planning").mkdir(parents=True)

    seen_roots: list[str] = []

    def _fake_grep(pat, search_root):
        seen_roots.append(search_root)
        return None  # no matches → probe returns None, fine for this assertion

    monkeypatch.setattr(compiler, "_grep_one_pattern", _fake_grep)

    compiler._probe_grep_for_task(
        "find every `log_swallowed` call",
        named_paths=["internal/planning"],
        cwd=cwd,
    )

    assert seen_roots, "probe should have attempted at least one grep"
    for root in seen_roots:
        assert not compiler._is_private_search_root(root, cwd)
        assert os.path.realpath(root) == os.path.realpath(cwd)


def test_probe_grep_uses_public_named_path(tmp_path, monkeypatch):
    """A public named directory is still honored as the search root."""
    cwd = str(tmp_path)
    pub = tmp_path / "src" / "roam"
    pub.mkdir(parents=True)

    seen_roots: list[str] = []

    def _fake_grep(pat, search_root):
        seen_roots.append(search_root)
        return None

    monkeypatch.setattr(compiler, "_grep_one_pattern", _fake_grep)

    compiler._probe_grep_for_task(
        "find every `log_swallowed` call",
        named_paths=["src/roam"],
        cwd=cwd,
    )

    assert seen_roots
    for root in seen_roots:
        assert os.path.realpath(root) == os.path.realpath(str(pub))
