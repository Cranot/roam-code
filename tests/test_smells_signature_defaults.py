"""Signature defaults contribute to the magic-number detector's counts."""

import sqlite3

import pytest

from roam.catalog.smells import detect_magic_numbers


@pytest.mark.parametrize("function_kind", ["def", "async def"])
@pytest.mark.parametrize(
    "signature, default, body_value",
    [
        ("timeout={}", "30", "30"),
        ("*, required, timeout={}", "30", "30"),
        ("timeout={}", "3.5", "3.5"),
        ("timeout={}", "-30", "-30"),
        # Existing compound-default and body-only behaviour are controls.
        ("timeout={}", "(30,)", "30"),
        ("timeout={}", "None", "30"),
    ],
)
def test_magic_numbers_include_signature_defaults(tmp_path, monkeypatch, function_kind, signature, default, body_value):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    # Two body occurrences plus one in the signature cross the threshold.
    # The None control instead has three body occurrences.
    values = [body_value] * (3 if default == "None" else 2)
    source = f"{function_kind} schedule({signature.format(default)}):\n    return {', '.join(values)}\n"
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE files (id INTEGER, path TEXT, language TEXT)")
        conn.execute("INSERT INTO files VALUES (1, 'sample.py', 'python')")
        findings = detect_magic_numbers(conn)
    finally:
        conn.close()

    assert len(findings) == 1
    assert findings[0]["symbol_name"] == "schedule"
    assert findings[0]["metric_value"] == 3
