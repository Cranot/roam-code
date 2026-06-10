"""W-BUGSITE — file:line bug-fix source slice (2026-06-10).

Telemetry: "fix the bug in cli.py:45" / "fix the AttributeError in
indexer.py" carried an explicit file:LINE but freeform embedded only
skeleton+grep — the agent had to Read the file to see the cited code.
The bug_site_slice probe embeds the ±N lines around the cited line (the
bug-fix analog of the W86 test-source slice). Edit/bug intent + a
path:line tuple are BOTH required so incidental path:line mentions in
non-edit prompts don't trigger it.
"""

from __future__ import annotations

from roam.plan.compiler import _freeform_bug_site_slice


def _write(tmp_path, rel, n_lines):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"line{i}\n" for i in range(1, n_lines + 1)))
    return p


class TestBugSiteSlice:
    def test_embeds_window_around_cited_line(self, tmp_path):
        _write(tmp_path, "src/mod.py", 100)
        facts = _freeform_bug_site_slice("fix the bug in src/mod.py:45", ["src/mod.py"], str(tmp_path))
        assert "bug_site_slice" in facts
        bs = facts["bug_site_slice"]
        assert bs["cited_line"] == 45
        assert bs["line_range"] == "33-57"  # 45-12 .. 45+12
        assert "line45" in bs["content"]
        assert "   45  " in bs["content"]  # gutter line number
        assert "do" not in facts["bug_site_slice_definition"].lower()[:2]

    def test_clamps_at_file_start(self, tmp_path):
        _write(tmp_path, "src/mod.py", 100)
        facts = _freeform_bug_site_slice("fix the bug in src/mod.py:3", ["src/mod.py"], str(tmp_path))
        assert facts["bug_site_slice"]["line_range"].startswith("1-")

    def test_requires_edit_intent(self, tmp_path):
        _write(tmp_path, "src/mod.py", 100)
        # path:line but NO edit/bug intent → no slice (e.g. a trace question)
        facts = _freeform_bug_site_slice("what runs at src/mod.py:45", ["src/mod.py"], str(tmp_path))
        assert facts == {}

    def test_requires_path_line(self, tmp_path):
        _write(tmp_path, "src/mod.py", 100)
        facts = _freeform_bug_site_slice("fix the bug in src/mod.py", ["src/mod.py"], str(tmp_path))
        assert facts == {}

    def test_falls_back_to_named_path_when_cited_unresolvable(self, tmp_path):
        _write(tmp_path, "src/mod.py", 100)
        # cited as a bare basename that doesn't exist as-is; named_paths has
        # the resolved repo path
        facts = _freeform_bug_site_slice("fix the bug in mod.py:45", ["src/mod.py"], str(tmp_path))
        assert "bug_site_slice" in facts
        assert facts["bug_site_slice"]["path"] == "src/mod.py"

    def test_missing_file_returns_empty(self, tmp_path):
        facts = _freeform_bug_site_slice("fix the bug in ghost.py:45", [], str(tmp_path))
        assert facts == {}
