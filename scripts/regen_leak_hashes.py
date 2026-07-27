#!/usr/bin/env python
"""Regenerate the committed hashed-term constants in
``internal_language_patterns.py`` from a local, gitignored literals file.

Stdlib-only, same constraint as the rest of the anti-leak tooling. Contains
NO literal secret values itself — it only reads them, at run time, from
``scripts/internal_language_literals.txt`` (gitignored; never commit it).
If that file is missing this script has nothing to do and exits with an
explanatory error.

Why this exists: hand-editing SHA-256 digests into
``internal_language_patterns.py`` risks a subtle drift between the
normalization used to CREATE a digest and the normalization used at SCAN
TIME to reproduce it — a drift that silently stops catching a real leak
with no visible symptom (the pattern still "exists", it just never matches
again). Routing every digest through this one script, which imports the
SAME ``_normalize_tokens`` / ``_hash_phrase`` helpers the scanner uses,
makes the two impossible to drift apart.

Usage:
    python scripts/regen_leak_hashes.py

Populate ``scripts/internal_language_literals.txt`` first (one
``label<TAB>literal`` pair per line; see the header comment written into a
fresh copy of that file, or the module docstring in
``internal_language_patterns.py``, "Optional private supplement"). This
script always hashes with the COMMITTED public salt (never
``ROAM_LEAK_SALT``) — the whole point of the committed digests is that they
must work standalone, with no private salt, in CI and in every public
clone. Paste the two printed blocks over the matching sections in
``internal_language_patterns.py`` and re-run the "all 12 still detected"
test (``tests/test_leak_gate_hashed_terms.py``) before committing.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _load_patterns_module():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(script_dir, "internal_language_patterns.py")
    spec = importlib.util.spec_from_file_location("internal_language_patterns", module_path)
    if spec is None or spec.loader is None:
        sys.stderr.write(f"ERROR: could not load {module_path}\n")
        sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = _load_patterns_module()
    literals_path = Path(mod.__file__).with_name(mod._PRIVATE_LITERALS_FILENAME)
    if not literals_path.is_file():
        sys.stderr.write(
            f"ERROR: {literals_path} not found.\n"
            "This file is gitignored and never committed — create it locally with\n"
            "'label<TAB>literal' lines (one real value per line, blank/'#' lines\n"
            "ignored) before running this script. See the module docstring in\n"
            'internal_language_patterns.py, "Optional private supplement".\n'
        )
        return 1

    salt = mod._PUBLIC_SALT  # always the committed public salt, never ROAM_LEAK_SALT
    entries: list[tuple[str, str, int]] = []
    leading: dict[str, set[int]] = {}
    raw = literals_path.read_text(encoding="utf-8")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "\t" not in line:
            continue
        label, literal = line.split("\t", 1)
        label = label.strip()
        # Case sensitivity is a per-label, matching-logic choice (mirrors
        # what each label's ORIGINAL regex actually required) -- not
        # derivable from the literal text alone. See _CASE_SENSITIVE_LABELS.
        case_sensitive = label in mod._CASE_SENSITIVE_LABELS
        tokens = mod._normalize_tokens(literal, casefold=not case_sensitive)
        if not tokens:
            sys.stderr.write(f"WARNING: literal for label {label!r} normalized to nothing, skipping\n")
            continue
        phrase = " ".join(tokens)
        h = mod._hash_phrase(salt, phrase)
        entries.append((label, h, len(tokens)))
        if len(tokens) > 1:
            first = tokens[0]
            # Store the leading hash in BOTH case forms: the scanner tries
            # both per candidate without knowing a term's case mode ahead
            # of time (see _candidate_hashes).
            for form in {first, first.casefold()}:
                leading.setdefault(mod._hash_phrase(salt, form), set()).add(len(tokens))

    print(f"# Regenerated from {len(entries)} entries in {literals_path.name} (public salt).")
    print("# Paste this block over _COMMITTED_HASHED_TERMS in internal_language_patterns.py:")
    print("_COMMITTED_HASHED_TERMS: tuple[tuple[str, str, int], ...] = (")
    for label, h, size in entries:
        print(f'    ("{label}", "{h}", {size}),')
    print(")")
    print()
    print("# Paste this block over _LEADING_MULTI_HASHES:")
    print("_LEADING_MULTI_HASHES: dict[str, tuple[int, ...]] = {")
    for h, sizes in leading.items():
        print(f"    {h!r}: {tuple(sorted(sizes))!r},")
    print("}")
    print()
    print(
        "# NOTE: if the standalone-abbreviation (adjacency-exempt) term changed,\n"
        "# update _ADJACENCY_EXEMPT_HASHES by hand — this script does not know\n"
        "# which label, if any, needs that exemption; that is a matching-logic\n"
        "# choice, not something derivable from the literals file alone."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
