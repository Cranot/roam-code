"""Detection-regression proof for the anti-leak gate's hashed literal terms.

``scripts/internal_language_patterns.py`` stores 12 literal-value patterns
(a personal path, a client codename, private domain identifiers, a
contributor handle, and similar) as salted SHA-256 digests rather than
plain regexes — see that module's docstring, "Hashed literal terms — what
this buys you, honestly" — so the denylist doesn't ship the very values it
exists to protect. Two different guarantees need two different tests:

* ``test_hashed_term_count_matches_expected`` — permanent, runs everywhere,
  needs no private data. It can't see WHAT was dropped, but it catches a
  silent drop (an entry quietly removed from ``_COMMITTED_HASHED_TERMS``).

* The rest of this file is the actual "did we lose detection" proof. It
  reads real plaintext values from a gitignored, never-committed file
  (``scripts/internal_language_literals.txt``) and SKIPS with a clear
  reason when that file is absent — the normal state for CI and every
  public clone. Putting the literals directly in THIS file instead would
  just relocate the leak from the catalogue to the test suite; the skip is
  the honest alternative to that.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root


def _patterns():
    script = repo_root() / "scripts" / "internal_language_patterns.py"
    spec = importlib.util.spec_from_file_location("internal_language_patterns", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_m = _patterns()

# Expected label -> variant count for the 12 patterns held as hashed literal
# terms. Update this alongside internal_language_patterns.py ONLY when a
# literal term is deliberately added or removed — never just to silence a
# failing count.
_EXPECTED_LABEL_COUNTS = {
    "personal-machine-path": 2,
    "internal-project-codename": 2,
    "internal-domain-term-a": 2,
    "internal-domain-term-b": 1,
    "internal-domain-term-c": 2,
    "internal-domain-term-d": 1,
    "internal-domain-abbreviation": 1,
    "session-memory-path": 1,
    "internal-policy-clause": 3,
    "legacy-identity-string": 1,
    "unlisted-contributor-handle": 4,
    "private-platform-name": 1,
}


def test_hashed_term_count_matches_expected() -> None:
    """Catch a silent drop from the hashed-term catalogue.

    Runs with no private data: it verifies the SHAPE of the catalogue
    (which labels, how many variants each) never shrinks unnoticed, even
    though it has no way to check the dropped value's identity.
    """
    counts: dict[str, int] = {}
    for label, _hash, _size in _m._COMMITTED_HASHED_TERMS:
        counts[label] = counts.get(label, 0) + 1
    assert counts == _EXPECTED_LABEL_COUNTS, (
        f"hashed-term catalogue drifted: got {counts}, expected {_EXPECTED_LABEL_COUNTS}"
    )
    assert len(_m._COMMITTED_HASHED_TERMS) == sum(_EXPECTED_LABEL_COUNTS.values())


def _literals_path() -> Path:
    return repo_root() / "scripts" / "internal_language_literals.txt"


def _load_literals(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "\t" not in line:
            continue
        label, literal = line.split("\t", 1)
        out.setdefault(label.strip(), []).append(literal)
    return out


@pytest.fixture
def literals() -> dict[str, list[str]]:
    path = _literals_path()
    if not path.is_file():
        pytest.skip(
            f"{path} absent (gitignored, never committed) -- expected in CI and "
            "every public clone. This proof only runs where a maintainer has "
            "populated the file locally; see internal_language_patterns.py's "
            "docstring, 'Optional private supplement'."
        )
    return _load_literals(path)


def test_all_hashed_literals_still_detected(literals: dict[str, list[str]]) -> None:
    """Every literal in the private file must still trip its expected label.

    Embeds each real value in a plain sentence (not the bare literal alone)
    so this exercises the actual line-scanning path, not just "hash of X
    equals hash of X".
    """
    missing_labels = set(_EXPECTED_LABEL_COUNTS) - set(literals)
    assert not missing_labels, f"literals file is missing label(s): {sorted(missing_labels)}"

    failures = []
    for label, values in literals.items():
        for literal in values:
            line = f"context line mentions {literal} in passing here"
            hits = _m.scan_text("synthetic.py", line)
            names = {h[0] for h in hits}
            if label not in names:
                failures.append((label, literal, names))
    assert not failures, f"no longer detected -- (label, literal, actual_hits): {failures}"


def test_standalone_abbreviation_exemption_still_applies(literals: dict[str, list[str]]) -> None:
    """The one adjacency-exempt term: bare form caught, compound form isn't.

    Mirrors the original regex's negative lookahead (``AFM_xyz``,
    ``provider_afm``, ``AFM-x``, ``AFM.x`` were never flagged; bare
    standalone use was).
    """
    values = literals.get("internal-domain-abbreviation")
    if not values:
        pytest.skip("no internal-domain-abbreviation literal in the literals file")
    literal = values[0]

    bare = f"the {literal} value must be validated"
    hits = _m.scan_text("synthetic.py", bare)
    assert "internal-domain-abbreviation" in {h[0] for h in hits}, "bare standalone form no longer caught"

    exempt_lines = [
        f"{literal}_xyz is a fine identifier",
        f"provider_{literal.lower()} is allowed too",
        f"{literal}-something should stay exempt",
        f"{literal}.something should stay exempt",
    ]
    for line in exempt_lines:
        hits = _m.scan_text("synthetic.py", line)
        assert not hits, f"compound identifier incorrectly flagged: {line!r} -> {[h[0] for h in hits]}"


def test_split_literal_across_concatenation_still_detected(literals: dict[str, list[str]]) -> None:
    """A literal defensively split across a string concatenation is still
    caught in a NON-whitelisted file.

    ``_RAW_WORD_RE`` extraction deliberately does not stop at quotes or
    operators between word-runs (see internal_language_patterns.py), so a
    literal split the way ``tests/test_secret_scan_hook.py`` splits its own
    fixture value (two half-strings joined by a ``+`` operator, specifically
    to keep the contiguous string out of THAT file's own tracked source)
    still gets reassembled by the scanner and caught elsewhere. Pin that
    capability here, in a file that is NOT whitelisted, so a future
    tokenizer "simplification" that stops bridging across operators would be
    caught by THIS test rather than silently regressing the real gate.
    """
    values = literals.get("internal-project-codename")
    if not values:
        pytest.skip("no internal-project-codename literal in the literals file")
    literal = next((v for v in values if "-" in v), values[0])
    prefix, _, suffix = literal.partition("-")
    if not suffix:
        pytest.skip("chosen literal has no hyphen to split on")

    # Mimics real Python source: two separate string literals joined by an
    # operator, never contiguous in this test file's own tracked text.
    line = f'internal_name = "{prefix}" + "-{suffix}"'
    hits = _m.scan_text("synthetic.py", line)
    names = {h[0] for h in hits}
    assert "internal-project-codename" in names, f"split literal no longer reassembled and caught: {line!r} -> {names}"
