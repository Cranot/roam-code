"""Adversarial corpus for the suppression file — the data-loss class, fuzzed.

The confirmed bug: save round-tripped `.roam-suppressions.yml` through a
lossy parse and REWROTE it, silently dropping entries. Save is now
append-only; this suite throws hostile inputs at the load→save→load cycle
and pins two invariants for every one of them:

1. save NEVER shrinks the file (existing bytes are preserved verbatim);
2. a round-trip never raises and never loses the rows that parsed before.
"""

from __future__ import annotations

from pathlib import Path

from roam.commands.suppression import is_suppressed, load_suppressions, save_suppression

ADVERSARIAL_FILES = [
    # Unquoted colon in reason (broke the tiny parser historically).
    "suppressions:\n  - rule: complexity\n    file: a.py\n    reason: floor at 19: irreducible\n    status: safe\n",
    # Foreign status vocabulary.
    "suppressions:\n  - rule: naming\n    file: b.py\n    reason: x\n    status: fp\n",
    # Extra unknown keys.
    "suppressions:\n  - rule: naming\n    file: c.py\n    reason: x\n    status: safe\n    sprint: 14\n    owner_team: core\n",
    # CRLF line endings.
    "suppressions:\r\n  - rule: naming\r\n    file: d.py\r\n    reason: x\r\n    status: safe\r\n",
    # Tabs as indentation (invalid YAML).
    "suppressions:\n\t- rule: naming\n\t  file: e.py\n",
    # Comment-interleaved entries.
    "# header\nsuppressions:\n  # entry one\n  - rule: naming\n    file: f.py\n    reason: x\n    status: safe\n  # trailing comment\n",
    # Unicode in reason + path.
    "suppressions:\n  - rule: naming\n    file: gr/λήψη.py\n    reason: τιμολόγια — δοκιμή\n    status: safe\n",
    # Missing required keys (file absent).
    "suppressions:\n  - rule: naming\n    reason: orphaned\n    status: safe\n",
    # Empty list.
    "suppressions:\n",
    # List item with only a dash.
    "suppressions:\n  -\n  - rule: naming\n    file: h.py\n    reason: x\n    status: safe\n",
    # Very long single line (8KB reason).
    "suppressions:\n  - rule: naming\n    file: i.py\n    reason: " + "x" * 8192 + "\n    status: safe\n",
    # Duplicate keys within an entry.
    "suppressions:\n  - rule: naming\n    rule: complexity\n    file: j.py\n    reason: x\n    status: safe\n",
    # Completely unparseable garbage.
    "{{{ not yaml at all \x00\x01",
    # Root is a list, not a mapping.
    "- rule: naming\n  file: k.py\n",
]


def test_roundtrip_never_raises_never_shrinks(tmp_path: Path):
    for i, content in enumerate(ADVERSARIAL_FILES):
        proj = tmp_path / f"case{i}"
        proj.mkdir()
        cfg = proj / ".roam-suppressions.yml"
        cfg.write_bytes(content.encode("utf-8"))
        original = cfg.read_bytes()

        before_rows = load_suppressions(proj)  # must not raise
        save_suppression(proj, "secrets", "zz_new.py", "fuzz append", "safe")
        after_bytes = cfg.read_bytes()

        # Invariant 1: append-only — the original CONTENT is contained in
        # the new file (nothing dropped). Newline style may normalize
        # (CRLF→LF via text-mode read/write) — content-lossless, allowed.
        norm_original = original.replace(b"\r\n", b"\n").rstrip(b"\n")
        norm_after = after_bytes.replace(b"\r\n", b"\n")
        assert norm_original in norm_after, f"case {i}: original content lost"

        # Invariant 2: previously-parsed rows still parse; the new row lands
        # whenever the file's tail accepts a list item.
        after_rows = load_suppressions(proj)
        before_keys = {(r.get("rule"), r.get("file")) for r in before_rows}
        after_keys = {(r.get("rule"), r.get("file")) for r in after_rows}
        lost = before_keys - after_keys
        assert not lost, f"case {i}: rows lost across save: {lost}"


def test_clean_file_roundtrip_grows_and_matches(tmp_path: Path):
    save_suppression(tmp_path, "naming", "a.py", "r1", "safe", line=5)
    save_suppression(tmp_path, "secrets", "b.py", "r2", "acknowledged", symbol="loadKey")
    rows = load_suppressions(tmp_path)
    assert len(rows) == 2
    assert is_suppressed(rows, "naming", "a.py", 6)  # ±3 tolerance
    assert is_suppressed(rows, "secrets", "b.py", 999, symbol="loadKey")
