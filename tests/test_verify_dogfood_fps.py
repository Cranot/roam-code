"""Verify false-positive regressions from the dogfood day.

Covers findings #6 (composable container complexity is advisory) and #8
(FoxPro skipped by the syntax rule; string contents are opaque) from the
external Vue/PHP repo dogfood. Finding #7 (irreducible retry-loop floor)
is mitigated by symbol-keyed suppressions (test_suppression_append_only).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

from roam.commands.cmd_verify import (  # noqa: E402
    _SYNTAX_SKIP_LANGS,
    SEVERITY_INFO,
    _check_complexity,
    _find_error_nodes,
)
from roam.db.connection import open_db  # noqa: E402

COMPOSABLE_TS = """\
export function useSyncDriver(opts: any) {
  let count = 0;
  const driveAxis = (axis: string) => {
    if (axis === "x") { count++; if (count > 3) { return 1; } }
    for (const ch of axis) {
      if (ch === "z") { count--; } else if (ch === "y") { count += 2; }
    }
    while (count > 10) { if (count % 2) { count -= 3; } else { count -= 1; } }
    return 0;
  };
  function countdown(n: number) {
    while (n > 0) {
      if (n % 2) { n -= 2; } else if (n % 3) { n -= 3; } else { n -= 1; }
      for (let i = 0; i < n; i++) { if (i > 5) { break; } }
    }
    return n;
  }
  const start = () => {
    if (opts.go) { driveAxis("x"); } else if (opts.stop) { countdown(3); }
    try { driveAxis("y"); } catch (e) { if (opts.strict) { throw e; } }
  };
  return { driveAxis, countdown, start };
}

export function plainHelper(items: string[]) {
  let total = 0;
  for (const item of items) {
    if (item.length > 3) {
      for (const ch of item) {
        if (ch === "x") { total++; } else if (ch === "y") { total--; }
        while (total > 50) { if (total % 2) { total -= 5; } else { total -= 1; } }
      }
    } else if (item === "z") {
      try { total += 1; } catch (e) { total = 0; }
    }
  }
  if (total > 10) { if (total > 20) { if (total > 30) { return 99; } } }
  return total;
}
"""


def _build(tmp_path: Path) -> Path:
    proj = tmp_path / "vueish"
    (proj / "src").mkdir(parents=True)
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src" / "driver.ts").write_text(COMPOSABLE_TS)
    git_init(proj)
    index_in_process(proj)
    return proj


def test_composable_container_complexity_is_advisory(tmp_path, monkeypatch):
    proj = _build(tmp_path)
    monkeypatch.chdir(proj)
    with open_db(readonly=True) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM files WHERE path LIKE '%driver.ts'")]
        result = _check_complexity(conn, ids, threshold=5)

    by_symbol = {v["symbol"]: v for v in result["violations"]}
    # The use* container is flagged INFO (advisory), never WARN/FAIL.
    assert "useSyncDriver" in by_symbol
    assert by_symbol["useSyncDriver"]["severity"] == SEVERITY_INFO
    assert "container" in by_symbol["useSyncDriver"]["message"]
    # A plain function over the threshold still gets the real severity.
    assert "plainHelper" in by_symbol
    assert by_symbol["plainHelper"]["severity"] != SEVERITY_INFO
    # INFO findings don't drag the score down.
    only_info = [v for v in result["violations"] if v["severity"] == SEVERITY_INFO]
    assert len(only_info) >= 1


def test_foxpro_excluded_from_syntax_rule():
    assert "foxpro" in _SYNTAX_SKIP_LANGS


def test_error_nodes_inside_strings_are_opaque():
    class FakeNode:
        def __init__(self, type_, children=()):
            self.type = type_
            self.children = list(children)

    # An ERROR nested inside a string literal: not reported.
    tree = FakeNode(
        "program",
        [FakeNode("string", [FakeNode("ERROR")]), FakeNode("function", [FakeNode("ERROR")])],
    )
    errors = _find_error_nodes(tree)
    assert len(errors) == 1  # only the one outside the string
