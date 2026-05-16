"""Dataflow tightening for ``detect_nested_lookup`` (W36 dogfood #7).

The detector historically fired on the triplet
``has_nested_loops + subscript_in_loops + loop_with_compare`` which
matches both real O(n*m) hash-joinable lookups AND streaming output
(row*col CSV emission) AND matrix render. The 2026-05 PHP dogfood
clocked ~85% false positives.

The fix adds a fourth required signal — ``loop_eq_with_dependent_write``
— set only when the inner loop body contains BOTH

  1. an equality comparison between two different per-iteration variables
     (``$e->code === $t->code`` / ``a['k'] == b['k']``), AND
  2. an accumulator write (assignment or call) gated by that equality.

Streaming output (no equality), bare equality with no write, and writes
without per-iteration equality all return signal=0 and are no longer
flagged.
"""

from __future__ import annotations

# Shape of the real PHP smell named in the dogfood:
# ``InsuranceReportController::annualFundSummary``. Per-iteration
# equality + a write gated by that equality.
_PHP_TRUE_FILTERED_LOOKUP = """<?php
class InsuranceReportController {
    public function annualFundSummary($templates, $existing, $skipCodes) {
        $matched = [];
        foreach ($templates as $t) {
            if (in_array($t->code, $skipCodes)) {
                continue;
            }
            foreach ($existing as $e) {
                if ($e->code === $t->code) {
                    if ($e->active) {
                        $matched[$t->id] = $e;
                    } else {
                        $matched[$t->id] = null;
                    }
                }
            }
        }
        return $matched;
    }
}
"""

# Streaming output: nested foreach * column emission. Pre-fix this would
# have flagged because the triplet (nested loops + subscript + compare
# from the ``!== null`` / ``> 0`` checks) all evaluate to 1. The new
# signal returns 0 because the if-condition is not an equality between
# two per-iter vars.
_PHP_STREAMING_OUTPUT = """<?php
class CsvExporter {
    public function emitCsv($rows, $columns, $delim) {
        $csv = [];
        foreach ($rows as $row) {
            foreach ($columns as $col) {
                if ($row[$col] !== null) {
                    if (strlen($row[$col]) > 0) {
                        $csv[] = $row[$col];
                    } else {
                        $csv[] = $delim;
                    }
                }
            }
        }
        return implode($delim, $csv);
    }
}
"""

# Equality on per-iteration keys but no dependent write — side-effect
# only. Not a lookup pattern.
_PHP_EQUALITY_ONLY = """<?php
class MatchPrinter {
    public function reportMatches($a, $b, $logger) {
        foreach ($a as $x) {
            foreach ($b as $y) {
                if ($x === $y) {
                    if ($logger) {
                        $logger->info("match");
                    } else {
                        echo "match";
                    }
                }
            }
        }
    }
}
"""

# Write inside conditional but the condition isn't a per-iteration
# equality — it's a unary threshold check. Not a lookup pattern.
_PHP_WRITE_WITHOUT_EQUALITY = """<?php
class Filter {
    public function filterAndCollect($a, $b, $threshold) {
        $out = [];
        foreach ($a as $x) {
            foreach ($b as $y) {
                if ($y > 0) {
                    if ($y > $threshold) {
                        $out[] = $y;
                    } else {
                        $out[] = $threshold;
                    }
                }
            }
        }
        return $out;
    }
}
"""


def _matches_name(hit: dict, target: str) -> bool:
    """Detector hits store the symbol under ``symbol_name`` (qualified or
    bare). Match by suffix so both ``InsuranceReportController\\annualFundSummary``
    and ``annualFundSummary`` pass.
    """
    sym = hit.get("symbol_name") or hit.get("symbol") or hit.get("name") or ""
    if not sym:
        return False
    tail = sym.rsplit("\\", 1)[-1].rsplit(".", 1)[-1].rsplit("::", 1)[-1]
    return tail == target


def test_true_filtered_lookup_flagged(project_factory, monkeypatch):
    """Real PHP filtered lookup (annualFundSummary shape): equality on
    per-iteration keys + a dependent write inside the conditional.
    Must be flagged."""
    proj = project_factory({"src/InsuranceReportController.php": _PHP_TRUE_FILTERED_LOOKUP})
    monkeypatch.chdir(proj)
    from roam.catalog.detectors import detect_nested_lookup
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        hits = detect_nested_lookup(conn)
        flagged = [h for h in hits if _matches_name(h, "annualFundSummary")]
        assert flagged, (
            "annualFundSummary is the canonical real-world hash-join "
            f"candidate; detector must flag it. Got hits: {hits}"
        )
        assert flagged[0]["task_id"] == "nested-lookup"


def test_streaming_output_not_flagged(project_factory, monkeypatch):
    """Streaming row*col output: pre-fix the triplet matched, but there's
    no per-iteration equality. New signal returns 0 — must NOT flag."""
    proj = project_factory({"src/CsvExporter.php": _PHP_STREAMING_OUTPUT})
    monkeypatch.chdir(proj)
    from roam.catalog.detectors import detect_nested_lookup
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        hits = detect_nested_lookup(conn)
        flagged = [h for h in hits if _matches_name(h, "emitCsv")]
        assert not flagged, (
            "emitCsv is a streaming output loop; intrinsically O(n*m) "
            f"but not hash-joinable. Must NOT be flagged. Got: {flagged}"
        )


def test_equality_alone_not_flagged(project_factory, monkeypatch):
    """Equality test on per-iteration keys without a dependent write
    (side-effect only) — not a lookup pattern. Must NOT be flagged."""
    proj = project_factory({"src/MatchPrinter.php": _PHP_EQUALITY_ONLY})
    monkeypatch.chdir(proj)
    from roam.catalog.detectors import detect_nested_lookup
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        hits = detect_nested_lookup(conn)
        flagged = [h for h in hits if _matches_name(h, "reportMatches")]
        assert not flagged, (
            f"reportMatches has equality but only side-effect output — not a join. Must NOT be flagged. Got: {flagged}"
        )


def test_write_without_equality_not_flagged(project_factory, monkeypatch):
    """Write inside a conditional whose predicate isn't a per-iteration
    equality — not a lookup pattern. Must NOT be flagged."""
    proj = project_factory({"src/Filter.php": _PHP_WRITE_WITHOUT_EQUALITY})
    monkeypatch.chdir(proj)
    from roam.catalog.detectors import detect_nested_lookup
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        hits = detect_nested_lookup(conn)
        flagged = [h for h in hits if _matches_name(h, "filterAndCollect")]
        assert not flagged, (
            "filterAndCollect writes inside a threshold check, not a "
            f"per-iter equality. Must NOT be flagged. Got: {flagged}"
        )
