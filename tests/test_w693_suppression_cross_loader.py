"""W693 cross-loader compat test: every suppression substrate must be
enumerated, and loaders that share an on-disk file must agree on its shape.

Why this exists
---------------

The W676 audit (2026-05-14) found four incompatible suppression parsers
living in parallel:

  1. ``commands.suppression``        -> ``.roam-suppressions.yml``
  2. ``commands.smells_suppress``    -> ``.roam/smells.suppress.yml``  (W658)
  3. ``commands.finding_suppress``   -> ``.roam/suppressions.json``    +
                                        ``.roamignore-findings`` + inline
  4. ``output.sarif._load_suppressions`` -> ``.roam/suppressions.json``

W691 (just shipped) unified the ``.roam/suppressions.json`` schema so
loaders #3 and #4 read the same file shape. This test pins that
unification and walks ``src/roam`` to catch a fifth loader silently
landing in the future.

The guardrail mirrors the discipline of LAW 11 / CONSTRAINT 8: parser
vocabularies are a closed enumeration, not free string composition, and
new entries must be a deliberate source-code edit.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from roam.commands.finding_suppress import (
    DEFAULT_SUPPRESSIONS_PATH,
    _load_per_finding_suppressions,
    load_per_finding_suppressions_typed,
)
from roam.commands.smells_suppress import (
    DEFAULT_SUPPRESS_PATH as SMELLS_SUPPRESS_PATH,
)
from roam.commands.smells_suppress import (
    load_smells_suppressions,
    load_smells_suppressions_typed,
)
from roam.commands.suppression import (
    _serialize_suppressions,
    load_suppressions,
    load_suppressions_typed,
    save_suppression,
)
from roam.output.sarif import _load_suppressions as sarif_load_suppressions
from roam.output.sarif import _load_suppressions_typed as sarif_load_suppressions_typed
from roam.policy.suppression_v2 import FindingIdSuppression

# ---------------------------------------------------------------------------
# Canonical enumeration: every suppression loader in roam.
#
# Tuple shape: (module_path, function_name, on_disk_file_relative_to_repo)
#
# When a fifth loader lands, add it here. The drift-guard below walks
# src/roam/ and fails if it finds anything that LOOKS like a suppression
# loader (def name + suppression-file string literal) that isn't enumerated.
# ---------------------------------------------------------------------------

_SUPPRESSION_LOADERS: tuple[tuple[str, str, str], ...] = (
    ("roam.commands.suppression", "load_suppressions", ".roam-suppressions.yml"),
    # W692 typed wrapper around load_suppressions — same on-disk file, typed view.
    ("roam.commands.suppression", "load_suppressions_typed", ".roam-suppressions.yml"),
    ("roam.commands.smells_suppress", "load_smells_suppressions", ".roam/smells.suppress.yml"),
    # W722 typed wrapper around load_smells_suppressions — same on-disk file, typed view.
    ("roam.commands.smells_suppress", "load_smells_suppressions_typed", ".roam/smells.suppress.yml"),
    ("roam.commands.finding_suppress", "_load_per_finding_suppressions", ".roam/suppressions.json"),
    # W723 typed wrapper around _load_per_finding_suppressions — same on-disk file, typed view.
    ("roam.commands.finding_suppress", "load_per_finding_suppressions_typed", ".roam/suppressions.json"),
    ("roam.output.sarif", "_load_suppressions", ".roam/suppressions.json"),
    # W723 typed wrapper around _load_suppressions — same on-disk file, typed view
    # (canonical dict shape only; legacy list/envelope shapes are NOT projected).
    ("roam.output.sarif", "_load_suppressions_typed", ".roam/suppressions.json"),
)

# Files that the enumerated loaders consume. The drift-guard ignores the
# auxiliary .roamignore-findings file (handled INSIDE finding_suppress via a
# private helper, not a separate top-level loader) and inline annotations
# (line-text matcher, not a file loader).
_KNOWN_SUPPRESSION_FILES = frozenset(
    {
        ".roam-suppressions.yml",
        ".roam/smells.suppress.yml",
        ".roam/suppressions.json",
        ".roamignore-findings",  # handled in-process by finding_suppress
    }
)


# ---------------------------------------------------------------------------
# Drift-guard: walk src/roam and surface anything that LOOKS like a
# suppression loader but isn't enumerated above.
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Locate the repo root (the directory containing src/roam/)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "roam").is_dir():
            return parent
    raise RuntimeError("could not locate repo root from " + str(here))


def _discover_suppression_loaders() -> set[tuple[str, str]]:
    """AST-scan src/roam/ for callables that look like suppression loaders.

    A function counts as a suppression loader when ALL of the following
    hold inside one ``.py`` file:

    * the file contains a string literal referencing one of the known
      suppression files (``.roam-suppressions.yml`` /
      ``.roam/smells.suppress.yml`` / ``.roam/suppressions.json`` /
      ``.roamignore-findings``); AND
    * the file defines a top-level ``def`` whose name contains both
      ``load`` and ``suppress`` (case-insensitive).

    Returns a set of ``(module_path, function_name)`` tuples in roam's
    dotted form. The drift-guard test asserts this set is a subset of
    the enumerated canon (modulo the documented carve-outs).
    """
    root = _repo_root()
    src = root / "src" / "roam"
    out: set[tuple[str, str]] = set()

    for path in src.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Cheap pre-filter: file must contain a suppression-file literal.
        if not any(needle in text for needle in _KNOWN_SUPPRESSION_FILES):
            continue

        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        module_rel = path.relative_to(src).with_suffix("").as_posix().replace("/", ".")
        module_dotted = f"roam.{module_rel}"

        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            lname = node.name.lower()
            if "load" not in lname or "suppress" not in lname:
                continue
            out.add((module_dotted, node.name))

    return out


def test_w693_drift_guard_loaders_enumerated():
    """Every load_*suppress* function in src/roam/ must be enumerated above.

    If this test fails, a new suppression loader landed without an entry
    in ``_SUPPRESSION_LOADERS``. Either add it (and decide whether it
    shares a file with an existing loader -> cross-loader compat case),
    or rename the function so it doesn't trip the heuristic.
    """
    discovered = _discover_suppression_loaders()
    declared = {(m, f) for (m, f, _file) in _SUPPRESSION_LOADERS}
    missing = discovered - declared

    # Drive the human-readable failure message with the canonical list so
    # the fix is obvious when this trips.
    assert not missing, (
        "W693 drift: new suppression loader(s) found but not enumerated in "
        f"_SUPPRESSION_LOADERS — {sorted(missing)}. Add them and decide "
        "whether the file is shared (cross-loader compat case)."
    )

    # The reverse direction is informational, not load-bearing — an
    # enumerated loader that the AST walk missed would be a regression in
    # the discovery heuristic, not in the substrate. Surface it as a soft
    # warning via the assertion message.
    extra = declared - discovered
    assert not extra, (
        "W693 discovery regression: enumerated loader(s) no longer detected "
        f"by the AST walk — {sorted(extra)}. The heuristic in "
        "_discover_suppression_loaders may need to widen."
    )


def test_w693_every_loader_is_importable_and_callable(tmp_path, monkeypatch):
    """Each enumerated loader must be importable AND callable on an empty
    project root without raising. Empty input -> empty result is the
    cheapest sanity check that the substrate is wired.
    """
    import importlib

    for module_path, func_name, _on_disk in _SUPPRESSION_LOADERS:
        mod = importlib.import_module(module_path)
        fn = getattr(mod, func_name, None)
        assert fn is not None, f"W693: enumerated loader {module_path}.{func_name} not found"
        assert callable(fn), f"W693: {module_path}.{func_name} is not callable"

    # Empty project root -> every loader returns its empty form.
    monkeypatch.chdir(tmp_path)

    assert load_suppressions(tmp_path) == []
    assert load_smells_suppressions(tmp_path) == []
    assert _load_per_finding_suppressions(tmp_path / ".roam" / "suppressions.json") == {}
    assert load_per_finding_suppressions_typed(tmp_path / ".roam" / "suppressions.json") == []
    # The SARIF loader is anchored to Path.cwd() — chdir above covers it.
    assert sarif_load_suppressions() == []
    assert sarif_load_suppressions_typed() == []


# ---------------------------------------------------------------------------
# Cross-loader equivalence: when two loaders share a file, the same
# on-disk content must produce semantically equivalent suppression intent.
#
# Post-W691, ``.roam/suppressions.json`` is shared by:
#   - roam.commands.finding_suppress._load_per_finding_suppressions
#   - roam.output.sarif._load_suppressions
# ---------------------------------------------------------------------------


def _write_canonical_suppressions(root: Path, entries: dict) -> Path:
    """Write a canonical W691 dict-shaped .roam/suppressions.json file."""
    target = root / ".roam" / "suppressions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")
    return target


def test_w693_shared_file_canonical_shape_both_loaders_agree(tmp_path, monkeypatch):
    """W691: canonical dict-shape consumed by BOTH finding_suppress + sarif.

    The two readers project the same file into different views:
      * ``finding_suppress`` keys by finding_id (the dict key);
      * ``sarif`` keys by (rule_id, location) and SKIPS entries that lack
        the SARIF projection fields.

    Semantic equivalence: every finding_id in the dict must appear in
    ``finding_suppress``'s output; every entry that carries rule_id AND
    location must appear in ``sarif``'s output with the same reason.
    """
    canonical = {
        # Entry with the SARIF projection fields — both loaders bind it.
        "a1b2c3d4e5f60718": {
            "reason": "depth-limited; verified",
            "added_at": "2026-05-14T00:00:00.000Z",
            "source": "from-finding",
            "rule_id": "math/branching-recursion",
            "location": "src/utils/object-diff.ts:42",
        },
        # Entry WITHOUT SARIF projection fields — finding_suppress keeps
        # it (finding_id is enough); SARIF correctly drops it (cannot
        # reverse the hash to (ruleId, location)).
        "f0e1d2c3b4a59687": {
            "reason": "vetted batch",
            "added_at": "2026-05-14T00:00:00.000Z",
        },
    }
    _write_canonical_suppressions(tmp_path, canonical)
    monkeypatch.chdir(tmp_path)

    fs_view = _load_per_finding_suppressions(tmp_path / ".roam" / "suppressions.json")
    sarif_view = sarif_load_suppressions()

    # finding_suppress sees BOTH entries (key = finding_id).
    assert set(fs_view) == set(canonical), (
        "finding_suppress must surface every dict entry, including ones "
        "without rule_id/location (those are still suppressed by hash)"
    )
    for fid, entry in canonical.items():
        assert fs_view[fid]["reason"] == entry["reason"]

    # sarif sees ONLY the entries that carry rule_id + location.
    sarif_keys = {(row["rule_id"], row["location"]) for row in sarif_view}
    expected_sarif_keys = {
        (e["rule_id"], e["location"]) for e in canonical.values() if e.get("rule_id") and e.get("location")
    }
    assert sarif_keys == expected_sarif_keys, (
        "SARIF loader must surface every canonical entry that carries rule_id+location, and only those"
    )

    # The shared reason text must round-trip identically (no quoting /
    # casing drift between the two readers).
    sarif_by_key = {(row["rule_id"], row["location"]): row for row in sarif_view}
    for entry in canonical.values():
        if not (entry.get("rule_id") and entry.get("location")):
            continue
        key = (entry["rule_id"], entry["location"])
        assert sarif_by_key[key]["reason"] == entry["reason"]


def test_w693_sarif_legacy_list_shape_still_supported(tmp_path, monkeypatch):
    """W691 keeps back-compat for the legacy list shape."""
    legacy = [
        {
            "rule_id": "math/io-in-loop",
            "location": "src/foo.py:10",
            "reason": "intentional",
        }
    ]
    target = tmp_path / ".roam" / "suppressions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    rows = sarif_load_suppressions()
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "math/io-in-loop"
    assert rows[0]["location"] == "src/foo.py:10"


def test_w693_sarif_legacy_envelope_shape_still_supported(tmp_path, monkeypatch):
    """W691 keeps back-compat for the ``{"suppressions": [...]}`` shape."""
    legacy_envelope = {
        "suppressions": [
            {
                "rule_id": "math/sort-to-select",
                "location": "src/bar.py:99",
                "reason": "audited",
            }
        ]
    }
    target = tmp_path / ".roam" / "suppressions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(legacy_envelope), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    rows = sarif_load_suppressions()
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "math/sort-to-select"


def test_w693_sarif_malformed_file_does_not_raise(tmp_path, monkeypatch):
    """All loaders MUST degrade silently on malformed input — never crash
    the analyser. Mirrors the discipline in suppression.py /
    smells_suppress.py / finding_suppress.py.
    """
    target = tmp_path / ".roam" / "suppressions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not valid json at all", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert sarif_load_suppressions() == []
    assert _load_per_finding_suppressions(target) == {}


# ---------------------------------------------------------------------------
# File-path-level smoke: every enumerated path is the one the matching
# loader actually consults. Catches a future rename of
# DEFAULT_SUPPRESSIONS_PATH / DEFAULT_SUPPRESS_PATH that doesn't update
# the enumeration above.
# ---------------------------------------------------------------------------


def test_w693_enumerated_paths_match_default_constants():
    """Defensive: the per-module default-path constants must equal the
    paths declared in ``_SUPPRESSION_LOADERS``.
    """
    by_func = {(m, f): p for (m, f, p) in _SUPPRESSION_LOADERS}

    assert by_func[("roam.commands.finding_suppress", "_load_per_finding_suppressions")] == str(
        DEFAULT_SUPPRESSIONS_PATH
    ).replace("\\", "/")
    assert by_func[("roam.commands.smells_suppress", "load_smells_suppressions")] == str(SMELLS_SUPPRESS_PATH).replace(
        "\\", "/"
    )


# ---------------------------------------------------------------------------
# YAML loaders: each has its own minimal parser. Pin the supported shape
# so a parser refactor that drops a field surfaces here.
# ---------------------------------------------------------------------------


def test_w693_roam_suppressions_yml_parses_minimal_entry(tmp_path):
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: secret-detection\n"
        "    file: tests/fixtures/fake_secrets.py\n"
        "    reason: Test fixtures with fake credentials\n"
        "    status: safe\n",
        encoding="utf-8",
    )
    rows = load_suppressions(tmp_path)
    assert len(rows) == 1
    assert rows[0]["rule"] == "secret-detection"
    assert rows[0]["status"] == "safe"


def test_w693_typed_wrapper_agrees_with_dict_loader(tmp_path):
    """W692: ``load_suppressions_typed`` returns ``RuleFileSuppression``
    instances over the same on-disk content. The typed view must surface
    the same row count + the same (rule, file) identity as the dict
    loader — i.e. it is a projection, not a parallel parser.
    """
    (tmp_path / ".roam-suppressions.yml").write_text(
        "suppressions:\n"
        "  - rule: secret-detection\n"
        "    file: tests/fixtures/fake.py\n"
        "    reason: fixture\n"
        "    status: safe\n"
        "  - rule: complexity-high\n"
        "    file: src/roam/index/indexer.py\n"
        "    line: 142\n"
        "    reason: intentional\n"
        "    status: acknowledged\n",
        encoding="utf-8",
    )
    dict_rows = load_suppressions(tmp_path)
    typed_rows = load_suppressions_typed(tmp_path)

    assert len(dict_rows) == len(typed_rows) == 2
    dict_ids = {(r["rule"], r["file"]) for r in dict_rows}
    typed_ids = {(r.rule, r.file) for r in typed_rows}
    assert dict_ids == typed_ids


def test_w722_typed_smells_loader_agrees_with_dict_loader(tmp_path):
    """W722 (W692 Phase B-a): ``load_smells_suppressions_typed`` returns
    :class:`KindSymbolSuppression` instances over the same on-disk content.

    Mirrors the discipline of ``test_w693_typed_wrapper_agrees_with_dict_loader``
    for the rule/file loader. The typed view must surface the same row
    count + the same (kind, symbol) identity as the dict loader — i.e. it
    is a projection, not a parallel parser. Hash-stability for the
    smells substrate's downstream evidence packets requires this contract.
    """
    target = tmp_path / ".roam" / "smells.suppress.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "suppressions:\n"
        "  - kind: shotgun-surgery\n"
        "    symbol: roam.languages.registry.get_language_for_file\n"
        '    reason: "Public API hub by design"\n'
        "  - kind: god-class\n"
        "    symbol: GodManager\n"
        "    file: src/roam/cli.py\n"
        '    reason: "Registry dispatch"\n'
        '    expires: "2099-01-01"\n',
        encoding="utf-8",
    )
    dict_rows = load_smells_suppressions(tmp_path)
    typed_rows = load_smells_suppressions_typed(tmp_path)

    assert len(dict_rows) == len(typed_rows) == 2
    dict_ids = {(r["kind"], r["symbol"]) for r in dict_rows}
    typed_ids = {(r.kind, r.symbol) for r in typed_rows}
    assert dict_ids == typed_ids
    # The discriminated-union source stamp must mark these as smells-suppress
    # entries (lets downstream consumers tell apart variants without sniffing
    # the match-key shape).
    assert all(r.source == "smells-suppress-yml" for r in typed_rows)


def test_w722_typed_smells_loader_round_trips_through_to_dict(tmp_path):
    """W722: ``KindSymbolSuppression.from_dict`` -> ``.to_dict`` preserves
    every field present on a W658 fixture row.

    The dataclass is the new internal representation. Its on-disk
    round-trip must NOT lose data — otherwise migration to the typed
    matcher (W724 Phase C) would silently drop fields.
    """
    target = tmp_path / ".roam" / "smells.suppress.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "suppressions:\n"
        "  - kind: shotgun-surgery\n"
        "    symbol: hub_fn\n"
        "    file: src/roam/languages/registry.py\n"
        '    reason: "Public API hub by design"\n'
        '    author: "dev@example.com"\n'
        '    added: "2026-02-25"\n'
        '    expires: "2099-01-01"\n',
        encoding="utf-8",
    )
    typed = load_smells_suppressions_typed(tmp_path)
    assert len(typed) == 1

    sup = typed[0]
    assert sup.kind == "shotgun-surgery"
    assert sup.symbol == "hub_fn"
    assert sup.file == "src/roam/languages/registry.py"
    assert sup.reason == "Public API hub by design"
    assert sup.author == "dev@example.com"
    assert sup.source == "smells-suppress-yml"

    projected = sup.to_dict()
    # Stable on-disk key order from the dataclass projection (kind first,
    # symbol second — matches the matcher's primary discriminator order).
    assert projected["kind"] == "shotgun-surgery"
    assert projected["symbol"] == "hub_fn"
    assert projected["file"] == "src/roam/languages/registry.py"
    assert projected["reason"] == "Public API hub by design"
    assert projected["added"] == "2026-02-25"
    assert projected["expires"] == "2099-01-01"


def test_w722_typed_smells_loader_empty_on_missing_file(tmp_path):
    """W722: typed loader must mirror the dict loader's empty-result
    discipline on a missing file. The dataclass migration MUST NOT make
    the loader crash on a project without ``.roam/smells.suppress.yml``.
    """
    # No file written.
    typed = load_smells_suppressions_typed(tmp_path)
    assert typed == []


def test_w693_smells_suppress_yml_parses_minimal_entry(tmp_path):
    target = tmp_path / ".roam" / "smells.suppress.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "suppressions:\n"
        "  - kind: shotgun-surgery\n"
        "    symbol: roam.languages.registry.get_language_for_file\n"
        '    reason: "Public API hub by design"\n',
        encoding="utf-8",
    )
    rows = load_smells_suppressions(tmp_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == "shotgun-surgery"
    assert rows[0]["symbol"].endswith("get_language_for_file")


# ---------------------------------------------------------------------------
# W707: save_suppression / _serialize_suppressions regression — multi-entry
# serialization must produce one dash-prefixed list item per suppression,
# round-trip cleanly through the parser, and never re-emit the W693-era
# dead-code shape (`first = True` immediately overwritten by `first = False`).
# ---------------------------------------------------------------------------


def test_w707_serialize_three_entries_round_trip():
    """``_serialize_suppressions`` must emit exactly one ``- `` dash per
    suppression and the YAML must round-trip through the parser without
    loss. Pre-fix the dead-code was harmless (the outer-loop reset masked
    the redundant assignment), but if the inner-loop reset were ever moved
    onto the wrong side of the emit, multi-entry serialization would fuse
    fields across entries. This test pins the contract.
    """
    data = [
        {
            "rule": "secret-detection",
            "file": "tests/fixtures/fake.py",
            "reason": "fixture",
            "status": "safe",
            "date": "2026-05-14",
        },
        {
            "rule": "complexity-high",
            "file": "src/roam/index/indexer.py",
            "line": 142,
            "reason": "intentional",
            "status": "acknowledged",
            "author": "dev@example.com",
            "date": "2026-05-14",
        },
        {
            "rule": "shotgun-surgery",
            "file": "src/roam/languages/registry.py",
            "reason": "public API hub by design",
            "status": "wont-fix",
            "date": "2026-05-14",
        },
    ]

    out = _serialize_suppressions(data)

    # Exactly one ``  - `` dash per entry.
    dash_count = sum(1 for line in out.splitlines() if line.startswith("  - "))
    assert dash_count == len(data), f"W707: expected {len(data)} list-item dashes, got {dash_count}"

    # Round-trip: parsing the serialized output yields the same rows.
    from roam.commands.suppression import _parse_suppressions_yaml

    parsed = _parse_suppressions_yaml(out)
    assert len(parsed) == len(data)
    for original, reloaded in zip(data, parsed):
        for field in ("rule", "file", "reason", "status", "date"):
            assert reloaded.get(field) == original.get(field), f"W707: field {field!r} did not round-trip"
        if "line" in original:
            assert reloaded.get("line") == original["line"]


def test_w707_save_suppression_then_load_round_trips_multiple_entries(tmp_path):
    """End-to-end: ``save_suppression`` appended N times must produce N
    distinct rows visible to ``load_suppressions``. Catches any regression
    where multi-entry persistence collapses rows due to mis-placed list
    delimiters in the serialiser.
    """
    save_suppression(
        tmp_path,
        rule="secret-detection",
        file="tests/fixtures/fake.py",
        reason="fixture",
        status="safe",
    )
    save_suppression(
        tmp_path,
        rule="complexity-high",
        file="src/roam/index/indexer.py",
        reason="intentional",
        status="acknowledged",
        line=142,
    )
    save_suppression(
        tmp_path,
        rule="shotgun-surgery",
        file="src/roam/languages/registry.py",
        reason="public API hub by design",
        status="wont-fix",
        author="dev@example.com",
    )

    rows = load_suppressions(tmp_path)
    assert len(rows) == 3, f"W707: expected 3 rows after 3 saves, got {len(rows)}"

    by_rule = {r["rule"]: r for r in rows}
    assert set(by_rule) == {
        "secret-detection",
        "complexity-high",
        "shotgun-surgery",
    }
    assert by_rule["complexity-high"].get("line") == 142
    assert by_rule["shotgun-surgery"].get("author") == "dev@example.com"


# ---------------------------------------------------------------------------
# W723 (W692 Phase B-b): typed finding_suppress + sarif loaders.
#
# Mirrors the discipline shipped for W692 (rule-file) and W722 (kind-symbol):
# the typed view must be a projection of the same on-disk file the dict
# loader consumes — never a parallel parser. Hash-stability for downstream
# evidence packets requires this contract.
# ---------------------------------------------------------------------------


def test_w723_typed_finding_suppress_loader_agrees_with_dict_loader(tmp_path, monkeypatch):
    """W723: ``load_per_finding_suppressions_typed`` returns
    :class:`FindingIdSuppression` instances over the same canonical
    ``.roam/suppressions.json`` content as the dict-keyed loader.

    Pins the projection contract for finding_suppress's typed view —
    same row count, same finding_id identity, in the order the dict
    loader surfaces them. Cross-loader equivalence is the W724 Phase C
    precondition for swapping the matcher to the dataclass.
    """
    canonical = {
        "a1b2c3d4e5f60718": {
            "reason": "depth-limited; verified",
            "added_at": "2026-05-14T00:00:00.000Z",
            "rule_id": "math/branching-recursion",
            "location": "src/utils/object-diff.ts:42",
            "task_id": "branching-recursion",
            "symbol_name": "diff",
        },
        # Hash-only entry — finding_suppress keeps it; SARIF typed view
        # drops it (parallels the dict-shaped invisibility contract).
        "f0e1d2c3b4a59687": {
            "reason": "vetted batch",
            "added_at": "2026-05-14T00:00:00.000Z",
        },
    }
    target = _write_canonical_suppressions(tmp_path, canonical)
    monkeypatch.chdir(tmp_path)

    dict_view = _load_per_finding_suppressions(target)
    typed_view = load_per_finding_suppressions_typed(target)

    # Same row count + same finding_id identity — typed is a projection,
    # not a parallel parser.
    assert len(dict_view) == len(typed_view) == 2
    assert {sup.finding_id for sup in typed_view} == set(dict_view)
    # The discriminated-union source stamp marks these as suppressions-json
    # entries (lets downstream consumers tell apart variants without
    # sniffing the match-key shape).
    assert all(sup.source == "suppressions-json" for sup in typed_view)

    by_fid = {sup.finding_id: sup for sup in typed_view}
    sarif_entry = by_fid["a1b2c3d4e5f60718"]
    assert sarif_entry.rule_id == "math/branching-recursion"
    assert sarif_entry.location == "src/utils/object-diff.ts:42"
    assert sarif_entry.task_id == "branching-recursion"
    assert sarif_entry.symbol_name == "diff"
    assert sarif_entry.reason == "depth-limited; verified"

    hash_only = by_fid["f0e1d2c3b4a59687"]
    assert hash_only.rule_id is None
    assert hash_only.location is None
    assert hash_only.reason == "vetted batch"


def test_w723_typed_finding_suppress_round_trips_through_to_dict(tmp_path):
    """W723: ``FindingIdSuppression.from_dict`` -> ``.to_dict`` preserves
    every field present on a W691 canonical entry.

    The dataclass is the new internal representation. Its on-disk
    round-trip must NOT lose data — otherwise migration to the typed
    matcher (W724 Phase C) would silently drop fields.
    """
    canonical = {
        "abc123def4567890": {
            "reason": "verified manually",
            "added_at": "2026-05-14T00:00:00.000Z",
            "rule_id": "algo/io-in-loop",
            "location": "src/foo.py:42",
            "task_id": "io-in-loop",
            "symbol_name": "MyClass.list",
        },
    }
    target = _write_canonical_suppressions(tmp_path, canonical)

    typed = load_per_finding_suppressions_typed(target)
    assert len(typed) == 1

    sup = typed[0]
    projected = sup.to_dict()
    assert projected["rule_id"] == "algo/io-in-loop"
    assert projected["location"] == "src/foo.py:42"
    assert projected["task_id"] == "io-in-loop"
    assert projected["symbol_name"] == "MyClass.list"
    assert projected["reason"] == "verified manually"
    # date-only round-trip (datetime collapses to YYYY-MM-DD).
    assert projected["added_at"] == "2026-05-14"


def test_w723_typed_sarif_loader_agrees_with_dict_loader(tmp_path, monkeypatch):
    """W723: ``_load_suppressions_typed`` returns the SARIF-visible subset
    of ``.roam/suppressions.json`` as :class:`FindingIdSuppression`
    instances.

    The dict-shaped legacy view filters the same way (drops entries
    without rule_id + location). The typed view MUST surface the same
    rows under (rule_id, location) identity — i.e. it is a projection,
    not a parallel parser.
    """
    canonical = {
        # Visible to BOTH typed + dict SARIF loaders.
        "a1b2c3d4e5f60718": {
            "reason": "depth-limited; verified",
            "added_at": "2026-05-14T00:00:00.000Z",
            "rule_id": "math/branching-recursion",
            "location": "src/utils/object-diff.ts:42",
        },
        # Visible to typed + dict SARIF loaders (second projection row).
        "11223344aabbccdd": {
            "reason": "audited fixture",
            "added_at": "2026-05-14T00:00:00.000Z",
            "rule_id": "secret-detection",
            "location": "tests/fixtures/fake.py:7",
        },
        # Hash-only — invisible to BOTH SARIF views.
        "f0e1d2c3b4a59687": {
            "reason": "vetted batch",
            "added_at": "2026-05-14T00:00:00.000Z",
        },
    }
    _write_canonical_suppressions(tmp_path, canonical)
    monkeypatch.chdir(tmp_path)

    dict_rows = sarif_load_suppressions()
    typed_rows = sarif_load_suppressions_typed()

    # SARIF-visible row count agrees: 2 canonical entries carry the
    # projection fields; the hash-only entry is invisible to both.
    assert len(dict_rows) == len(typed_rows) == 2
    assert all(isinstance(row, FindingIdSuppression) for row in typed_rows)

    dict_keys = {(row["rule_id"], row["location"]) for row in dict_rows}
    typed_keys = {(sup.rule_id, sup.location) for sup in typed_rows}
    assert dict_keys == typed_keys, (
        "W723: SARIF typed view must project the same (rule_id, location) identities as the dict-shaped view"
    )

    # The shared reason text must round-trip identically between the
    # two readers — no quoting / casing drift.
    dict_reasons = {(r["rule_id"], r["location"]): r["reason"] for r in dict_rows}
    for sup in typed_rows:
        assert dict_reasons[(sup.rule_id, sup.location)] == sup.reason

    # Discriminated-union source stamp.
    assert all(sup.source == "suppressions-json" for sup in typed_rows)


def test_w723_typed_sarif_loader_does_not_project_legacy_shapes(tmp_path, monkeypatch):
    """W723: the typed SARIF view is canonical-only.

    Legacy on-disk shapes (top-level list / ``{"suppressions": [...]}``
    envelope) pre-date the W691 finding_id discriminator and cannot be
    projected onto :class:`FindingIdSuppression` without invention.
    The dict-shaped SARIF loader still surfaces them for back-compat
    (covered by ``test_w693_sarif_legacy_list_shape_still_supported``);
    the typed view must return ``[]`` instead of inventing finding_ids.
    """
    # Top-level list (legacy SARIF).
    legacy_list = [
        {
            "rule_id": "math/io-in-loop",
            "location": "src/foo.py:10",
            "reason": "intentional",
        }
    ]
    target = tmp_path / ".roam" / "suppressions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(legacy_list), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # Dict shape: still surfaces the legacy row for back-compat.
    assert len(sarif_load_suppressions()) == 1
    # Typed shape: canonical-only; legacy list shape is invisible.
    assert sarif_load_suppressions_typed() == []

    # Envelope shape: same discipline.
    legacy_envelope = {
        "suppressions": [
            {
                "rule_id": "math/sort-to-select",
                "location": "src/bar.py:99",
                "reason": "audited",
            }
        ]
    }
    target.write_text(json.dumps(legacy_envelope), encoding="utf-8")
    assert len(sarif_load_suppressions()) == 1
    assert sarif_load_suppressions_typed() == []


def test_w736_typed_sarif_applier_is_byte_equivalent_to_dict_applier(tmp_path, monkeypatch):
    """W736 (Phase C-1a of W692): the typed SARIF applier produces
    byte-identical output to the legacy dict applier on the same fixture.

    The SARIF hash-stability mandate requires that the typed migration
    does not flip any byte in the rendered SARIF envelope — anywhere a
    consumer is hashing the output (CI, release attestation, snapshot
    tests) is downstream of this guarantee.
    """
    import json as _json

    from roam.output.sarif import (
        _apply_suppressions,
        _apply_suppressions_typed,
        _load_suppressions,
        _load_suppressions_typed,
    )

    canonical = {
        # Standard canonical row — reason + added_at only (writer default).
        "a1b2c3d4e5f60718": {
            "reason": "depth-limited; verified",
            "added_at": "2026-05-14T00:00:00.000Z",
            "rule_id": "math/branching-recursion",
            "location": "src/utils/object-diff.ts:42",
        },
        # Row with an explicit canonical status — exercise the status
        # passthrough (typed loader keeps "safe"; default path drops to
        # "accepted" via coercion).
        "11223344aabbccdd": {
            "reason": "audited fixture",
            "added_at": "2026-05-14T00:00:00.000Z",
            "rule_id": "secret-detection",
            "location": "tests/fixtures/fake.py:7",
            "status": "safe",
        },
        # Hash-only entry — invisible to BOTH loaders by design.
        "f0e1d2c3b4a59687": {
            "reason": "vetted batch",
            "added_at": "2026-05-14T00:00:00.000Z",
        },
    }
    _write_canonical_suppressions(tmp_path, canonical)
    monkeypatch.chdir(tmp_path)

    # Two SARIF results — one matches a suppression, one does not. Plus
    # one that matches the status="safe" row, to exercise the status
    # passthrough on both appliers.
    base_results = [
        {
            "ruleId": "math/branching-recursion",
            "level": "warning",
            "message": {"text": "depth"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "src/utils/object-diff.ts"},
                        "region": {"startLine": 42},
                    }
                }
            ],
        },
        {
            "ruleId": "secret-detection",
            "level": "error",
            "message": {"text": "secret"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "tests/fixtures/fake.py"},
                        "region": {"startLine": 7},
                    }
                }
            ],
        },
        {
            "ruleId": "math/some-other-rule",
            "level": "warning",
            "message": {"text": "unmatched"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "src/other.py"},
                        "region": {"startLine": 99},
                    }
                }
            ],
        },
    ]

    # Deep-copy via JSON round-trip so each applier mutates a fresh list.
    dict_input = _json.loads(_json.dumps(base_results))
    typed_input = _json.loads(_json.dumps(base_results))

    dict_out = _apply_suppressions(dict_input, _load_suppressions())
    typed_out = _apply_suppressions_typed(typed_input, _load_suppressions_typed())

    # Byte-identity: the rendered JSON of both result lists must match
    # exactly. sort_keys + identical separators pin any field-ordering
    # drift, but the applier mutates in place so dict iteration order
    # should already match.
    dict_bytes = _json.dumps(dict_out, sort_keys=True, separators=(",", ":")).encode("utf-8")
    typed_bytes = _json.dumps(typed_out, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert dict_bytes == typed_bytes, (
        "W736: typed SARIF applier must produce byte-identical output. "
        f"Dict applier:\n{dict_out}\nTyped applier:\n{typed_out}"
    )

    # Belt-and-braces structural check: both matched results carry the
    # suppressions array; the unmatched result does not.
    assert "suppressions" in dict_out[0] and "suppressions" in typed_out[0]
    assert "suppressions" in dict_out[1] and "suppressions" in typed_out[1]
    assert "suppressions" not in dict_out[2] and "suppressions" not in typed_out[2]

    # Status passthrough: row[1] has status="safe"; row[0] defaults to
    # "accepted" (writer left status unset → coerced to None → default).
    assert dict_out[0]["suppressions"][0]["status"] == "accepted"
    assert typed_out[0]["suppressions"][0]["status"] == "accepted"
    assert dict_out[1]["suppressions"][0]["status"] == "safe"
    assert typed_out[1]["suppressions"][0]["status"] == "safe"


def test_w737_typed_smells_applier_is_byte_equivalent_to_dict_applier(tmp_path):
    """W737 (Phase C-1b of W692): the typed smells applier produces
    byte-identical output to the legacy dict applier on the same fixture.

    The smells envelope hash-stability mandate requires that the typed
    migration in ``cmd_smells`` does not flip any byte in the rendered
    envelope (``suppressed_smells[]`` rows + the ``_suppressed_by``
    annotation each carries). This pins the byte-identity contract
    between :func:`apply_suppressions` (dict) and
    :func:`apply_suppressions_typed` (KindSymbolSuppression) on the same
    on-disk fixture, mirroring the W736 SARIF test.
    """
    import json as _json
    from datetime import date

    from roam.commands.smells_suppress import (
        apply_suppressions,
        apply_suppressions_typed,
        load_smells_suppressions,
        load_smells_suppressions_typed,
    )

    # Fixture: three rows exercising every branch of the matcher.
    #
    #   * Row 1 — bare-symbol match, no reason, no expires.
    #   * Row 2 — qualified-symbol match, with reason + expires (future).
    #   * Row 3 — file-suffix match, with reason.
    #
    # Plus one expired entry that must be skipped on the chosen ``today``.
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir(parents=True, exist_ok=True)
    (roam_dir / "smells.suppress.yml").write_text(
        """\
suppressions:
  - kind: shotgun-surgery
    symbol: hub
  - kind: god-class
    symbol: roam.languages.registry.GodManager
    reason: "Public API hub by design"
    expires: "2030-01-01"
  - kind: brain-method
    symbol: complicated
    file: src/foo.py
    reason: "audited fixture"
  - kind: shotgun-surgery
    symbol: expired_hub
    expires: "2020-01-01"
""",
        encoding="utf-8",
    )

    # Four findings covering: bare match, qualified match, file-suffix
    # match, expired-skip, and a no-match passthrough.
    findings_fixture = [
        {"smell_id": "shotgun-surgery", "symbol_name": "hub", "location": "a.py:1"},
        {"smell_id": "god-class", "symbol_name": "GodManager", "location": "b.py:2"},
        {"smell_id": "brain-method", "symbol_name": "complicated", "location": "src/foo.py:3"},
        # Same kind/symbol as the expired entry — must NOT be suppressed.
        {"smell_id": "shotgun-surgery", "symbol_name": "expired_hub", "location": "c.py:4"},
        # No-match passthrough.
        {"smell_id": "god-class", "symbol_name": "Other", "location": "d.py:5"},
    ]

    dict_suppressions = load_smells_suppressions(tmp_path)
    typed_suppressions = load_smells_suppressions_typed(tmp_path)

    # Pin ``today`` so the expired-entry branch is exercised
    # deterministically regardless of when the test runs.
    today = date(2026, 5, 14)

    # Deep-copy via JSON round-trip so each applier mutates a fresh list.
    dict_input = _json.loads(_json.dumps(findings_fixture))
    typed_input = _json.loads(_json.dumps(findings_fixture))

    dict_kept, dict_suppressed = apply_suppressions(dict_input, dict_suppressions, today=today)
    typed_kept, typed_suppressed = apply_suppressions_typed(typed_input, typed_suppressions, today=today)

    # Byte-identity on both halves of the partition. sort_keys + tight
    # separators pin any field-ordering drift; the appliers preserve
    # insertion order so this should already match without sort_keys,
    # but the sorted form is the relevant hash-stability claim.
    kept_dict_bytes = _json.dumps(dict_kept, sort_keys=True, separators=(",", ":")).encode("utf-8")
    kept_typed_bytes = _json.dumps(typed_kept, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert kept_dict_bytes == kept_typed_bytes, (
        "W737: typed smells applier must produce byte-identical KEPT rows. "
        f"Dict applier:\n{dict_kept}\nTyped applier:\n{typed_kept}"
    )

    sup_dict_bytes = _json.dumps(dict_suppressed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sup_typed_bytes = _json.dumps(typed_suppressed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert sup_dict_bytes == sup_typed_bytes, (
        "W737: typed smells applier must produce byte-identical SUPPRESSED rows "
        "(including the _suppressed_by annotation). "
        f"Dict applier:\n{dict_suppressed}\nTyped applier:\n{typed_suppressed}"
    )

    # Belt-and-braces structural checks: three findings suppressed, two
    # kept (the expired-entry finding + the unmatched no-match passthrough).
    assert len(typed_suppressed) == 3
    assert len(typed_kept) == 2
    suppressed_symbols = {row["symbol_name"] for row in typed_suppressed}
    assert suppressed_symbols == {"hub", "GodManager", "complicated"}
    kept_symbols = {row["symbol_name"] for row in typed_kept}
    assert kept_symbols == {"expired_hub", "Other"}

    # Annotation shape: the _suppressed_by dict carries kind/symbol/
    # reason/expires keys identically between paths. Spot-check the
    # row with a future ``expires`` — the dict path stores the raw
    # YAML string; the typed path projects via :meth:`date.isoformat`.
    # Both must serialise to the same ISO ``YYYY-MM-DD`` form.
    god_row_typed = next(r for r in typed_suppressed if r["symbol_name"] == "GodManager")
    god_row_dict = next(r for r in dict_suppressed if r["symbol_name"] == "GodManager")
    assert god_row_typed["_suppressed_by"]["expires"] == "2030-01-01"
    assert god_row_dict["_suppressed_by"]["expires"] == "2030-01-01"
    assert god_row_typed["_suppressed_by"] == god_row_dict["_suppressed_by"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-v"]))
