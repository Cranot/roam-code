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
    python scripts/regen_leak_hashes.py            # print the two blocks
    python scripts/regen_leak_hashes.py --check    # fail if they have drifted

Populate ``scripts/internal_language_literals.txt`` first (one
``label<TAB>literal`` pair per line; see the header comment written into a
fresh copy of that file, or the module docstring in
``internal_language_patterns.py``, "Private supplement"). This script always
hashes with the COMMITTED public salt (never ``ROAM_LEAK_SALT``) — the whole
point of the committed digests is that they must work standalone, with no
private salt, in CI and in every public clone. Paste the two printed blocks
over the matching sections in ``internal_language_patterns.py`` and re-run
``tests/test_leak_gate_hashed_terms.py`` before committing.

``--check`` closes the gap that printing alone leaves open. Printing a block
a human then pastes is a manual step, and a skipped manual step here has no
symptom: the catalogue still parses, the gate still runs on every push, and
it simply stops matching a value nobody notices is missing. That is precisely
how the platform's post-rename name went undetected in tracked PUBLIC files
while ``scan_internal_language.py --all`` exited 0. ``--check`` recomputes
from the literals file and compares against the committed constants:

    exit 0  in sync
    exit 1  DRIFTED — the committed digests no longer match the literals file
    exit 2  usage error
    exit 3  UNCHECKABLE — no literals file present, so nothing was compared

Exit 3 is deliberately not 0. The literals file is absent in CI and in every
public clone, and reporting "in sync" after comparing nothing would be the
same false-clean this whole module exists to prevent. Callers that legitimately
cannot check (CI) must treat 3 as "skipped", never as "passed".

``--check`` prints only labels, digests and counts on failure — never a
literal. A drift report must not become the leak.
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


def _compute(mod, literals_path: Path) -> tuple[list[tuple[str, str, int]], dict[str, set[int]]]:
    """Hash every literal in *literals_path* exactly as the scanner would.

    Shared by the print path and ``--check`` on purpose: a checker that
    recomputed digests its own way would be comparing two implementations
    rather than detecting drift.
    """
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
    return entries, leading


def _check(mod, literals_path: Path) -> int:
    """Compare the committed constants against the literals file.

    Order-insensitive on purpose: the committed blocks are grouped and
    commented by hand for readability, and reordering them changes nothing
    about what the gate matches. Only membership and n-gram size are
    load-bearing, so only those are compared.

    Prints labels, digests and counts — never a literal. A drift report that
    printed the plaintext would relocate the leak into CI logs.
    """
    entries, leading = _compute(mod, literals_path)
    failures: list[str] = []

    want_terms = sorted(entries)
    got_terms = sorted(mod._COMMITTED_HASHED_TERMS)
    if want_terms != got_terms:
        missing = [t for t in want_terms if t not in got_terms]
        extra = [t for t in got_terms if t not in want_terms]
        for label, h, size in missing:
            failures.append(f"  MISSING from _COMMITTED_HASHED_TERMS: ({label!r}, {h!r}, {size})")
        for label, h, size in extra:
            failures.append(f"  STALE in _COMMITTED_HASHED_TERMS (no literal produces it): ({label!r}, {h!r}, {size})")

    want_leading = {h: tuple(sorted(sizes)) for h, sizes in leading.items()}
    got_leading = {h: tuple(sorted(sizes)) for h, sizes in mod._LEADING_MULTI_HASHES.items()}
    for h in sorted(set(want_leading) | set(got_leading)):
        if want_leading.get(h) != got_leading.get(h):
            failures.append(
                f"  _LEADING_MULTI_HASHES[{h!r}]: committed {got_leading.get(h)!r}, literals give {want_leading.get(h)!r}"
            )

    if failures:
        sys.stderr.write(
            f"DRIFT: the committed hashed-term constants in {Path(mod.__file__).name} do not\n"
            f"match {literals_path.name}. The gate is matching a different set of values\n"
            "than the maintainer's source of truth says it should.\n\n"
        )
        sys.stderr.write("\n".join(failures) + "\n\n")
        sys.stderr.write("Fix: run `python scripts/regen_leak_hashes.py` and paste both blocks.\n")
        return 1

    print(f"IN SYNC: {len(entries)} literals -> {len(got_terms)} committed digests, {len(got_leading)} leading hashes.")
    return 0


def main(argv: list[str]) -> int:
    unknown = [a for a in argv if a != "--check"]
    if unknown:
        sys.stderr.write(f"ERROR: unknown argument(s): {' '.join(unknown)}\n")
        sys.stderr.write("Usage: python scripts/regen_leak_hashes.py [--check]\n")
        return 2
    check_only = "--check" in argv

    mod = _load_patterns_module()
    literals_path = Path(mod.__file__).with_name(mod._PRIVATE_LITERALS_FILENAME)
    if not literals_path.is_file():
        if check_only:
            sys.stderr.write(
                f"UNCHECKABLE: {literals_path.name} is absent, so nothing was compared.\n"
                "This is the normal state in CI and in every public clone (the file is\n"
                "gitignored and never committed). Treat this as SKIPPED, never as a pass.\n"
            )
            return 3
        sys.stderr.write(
            f"ERROR: {literals_path} not found.\n"
            "This file is gitignored and never committed — create it locally with\n"
            "'label<TAB>literal' lines (one real value per line, blank/'#' lines\n"
            "ignored) before running this script. See the module docstring in\n"
            'internal_language_patterns.py, "Private supplement".\n'
        )
        return 1

    if check_only:
        return _check(mod, literals_path)

    entries, leading = _compute(mod, literals_path)

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
    raise SystemExit(main(sys.argv[1:]))
