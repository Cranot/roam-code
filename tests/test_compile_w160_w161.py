"""W160-W161 — concrete-noun callers summary + target-symbol body embed."""

from __future__ import annotations

from roam.plan import compiler as M


def test_w160_callers_definition_concrete_noun_anchored(tmp_path, monkeypatch):
    """callers_definition string ends on a concrete noun (LAW 4)."""
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"_": "stub"})
    monkeypatch.setattr(
        M,
        "_flatten_consumers",
        lambda d: [
            "src/a.py:1",
            "src/b.py:2",
            "src/c.py:3",
            "src/d.py:4",
            "src/e.py:5",
            "src/f.py:6",
        ],
    )
    facts = M._probe_callers(["my_func"], cwd=str(tmp_path))
    assert "callers_definition" in facts
    s = facts["callers_definition"]
    assert "6 callers of `my_func`" in s
    assert "src/a.py:1" in s


def test_w160_callers_definition_handles_dict_callers(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"_": "stub"})
    monkeypatch.setattr(
        M,
        "_flatten_consumers",
        lambda d: [
            {"location": "src/a.py:42", "name": "use_a"},
            {"location": "src/b.py:7", "name": "use_b"},
        ],
    )
    facts = M._probe_callers(["my_func"], cwd=str(tmp_path))
    assert "callers_definition" in facts
    assert "src/a.py:42" in facts["callers_definition"]
    assert "2 callers" in facts["callers_definition"]


def test_w161_target_symbol_body_embedded_when_def_present(tmp_path, monkeypatch):
    """When target symbol's def is in named_paths file, embed ~40 lines."""
    (tmp_path / "src").mkdir()
    src = tmp_path / "src" / "mod.py"
    body_lines = (
        ["# header\n"] * 10
        + [
            "def target_fn():\n",
            '    """docstring"""\n',
            "    return 42\n",
        ]
        + ["# more\n"] * 50
    )
    src.write_text("".join(body_lines))
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"_": "stub"})
    monkeypatch.setattr(M, "_flatten_consumers", lambda d: ["src/x.py:1"])
    facts = M._probe_callers(
        ["target_fn", "src/mod.py"],
        cwd=str(tmp_path),
    )
    assert "target_symbol_body" in facts
    assert "def target_fn" in facts["target_symbol_body"]
    assert "target_symbol_body_definition" in facts


def test_w161_no_target_body_when_no_py_path(tmp_path, monkeypatch):
    """If named_paths has no .py file, skip embedding."""
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"_": "stub"})
    monkeypatch.setattr(M, "_flatten_consumers", lambda d: ["src/x.py:1"])
    facts = M._probe_callers(["bare_symbol"], cwd=str(tmp_path))
    assert "target_symbol_body" not in facts


def test_w161_no_target_body_when_cwd_none(monkeypatch):
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"_": "stub"})
    monkeypatch.setattr(M, "_flatten_consumers", lambda d: ["x.py:1"])
    facts = M._probe_callers(["sym", "src/foo.py"], cwd=None)
    assert "target_symbol_body" not in facts


def test_w161_target_body_fallback_to_first_120_lines(tmp_path, monkeypatch):
    """If symbol def not found in file, fall back to first 120 lines."""
    (tmp_path / "src").mkdir()
    src = tmp_path / "src" / "mod.py"
    # File doesn't actually contain `def missing_symbol(` — should fall back
    src.write_text("\n".join([f"line {i}" for i in range(200)]))
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"_": "stub"})
    monkeypatch.setattr(M, "_flatten_consumers", lambda d: ["src/x.py:1"])
    facts = M._probe_callers(["missing_symbol", "src/mod.py"], cwd=str(tmp_path))
    assert "target_symbol_body" in facts
    # First 120 lines included
    assert "line 0" in facts["target_symbol_body"]
    assert "line 119" in facts["target_symbol_body"]
