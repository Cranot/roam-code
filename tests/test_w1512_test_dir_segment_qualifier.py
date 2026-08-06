"""W1512 — the segment-and-qualify test-directory rule, pinned in both directions.

W1511 replaced an unanchored substring match with an exact / delimiter /
camelCase component match. Two independent adversarial reviews refuted it in
BOTH directions, and this module pins the refutation corpus so neither half can
regress:

* **still True, must be False.** ``bench-repos/laravel/src/Illuminate/Testing/``
  is a SHIPPED Composer package (``illuminate/testing``, 74 production ``.php``
  files across four ``Testing/`` directories), not a test suite;
  ``SupportTesting/``, ``image-spec/``, ``openapi-spec/``, ``specs-go/`` and
  ``tokio-test/`` (a shipped helper crate) are likewise production.
  ``backTesting`` returned True while the same commit's own unit table asserted
  ``("backtesting", False)`` and ``("Backtesting", False)`` — case-inconsistent
  with itself.

* **still False, must be True.** ``unittests/`` (LLVM, Chromium),
  ``unittest/``, ``jstests/`` (MongoDB's entire JS correctness suite),
  ``apitests/``, ``smoketests/``, ``e2etests/``, ``regressiontests/``,
  ``acceptancetests/``, ``systemtest/`` and ``foo__tests__/``.

The rule now segments a path component on delimiters (``-_. ``) and camelCase
boundaries, then asks a qualifier question about the segment preceding the test
token. ``unittests`` = ``unit`` + ``tests`` (qualified); ``backtests`` =
``back`` + ``tests`` (not qualified) — same token, different qualifier. That is
the only lexical signal available and it is why the *plural* collision class
(``backtests`` ``contests`` ``protests`` ``attests`` ``latests``) is pinned
here on day one: it is the original ``attest/`` defect one character over, in
the very codebase that was burned by it.

The property test at the bottom replaces W1511's Direction-3 test, which
iterated a hand-written path list and was therefore structurally incapable of
finding the ``$``-vs-``\\Z`` anchor bug it was supposed to guard
(``_TEST_DIR_CAMEL`` used ``$``, so ``"MyTest\\n"`` matched). ``hypothesis`` is
not in this project's venv, so the generator is a seeded ``random.Random``
over three strata.
"""

from __future__ import annotations

import random
import string

import pytest

from roam.commands.changed_files import _is_test_dir_component, is_test_file

# ---------------------------------------------------------------------------
# Corpus, direction 1 — MUST be False. Every path was measured on a real file
# in a real checkout, not imagined.
# ---------------------------------------------------------------------------

MUST_BE_FALSE = [
    # Laravel ships illuminate/testing as a package. Four Testing/ directories,
    # 74 production .php files. The capital T is the whole signal: a PHP
    # namespace segment, not a directory convention.
    "bench-repos/laravel/src/Illuminate/Testing/Assert.php",
    "bench-repos/laravel/src/Illuminate/Http/Testing/MimeType.php",
    "bench-repos/laravel/src/Illuminate/Foundation/Testing/TestCase.php",
    "bench-repos/laravel/src/Illuminate/Support/Testing/Fakes/QueueFake.php",
    # Livewire's shipped testing-support feature.
    "vendor/livewire/livewire/src/Features/SupportTesting/Render.php",
    # "spec" as in specification, not RSpec.
    "third-party/github.com/opencontainers/image-spec/specs-go/v1/config.go",
    "api/openapi-spec/swagger.json",
    # tokio-test is a published helper crate on crates.io.
    "bench-repos/tokio/tokio-test/src/io.rs",
    # The W1511 collision class, still False.
    "latest/build.py",
    "releases/latest/server.py",
    "app/contest/scoring.py",
    "src/greatest/x.js",
    "src/roam/attest/vsa.py",
    "pytest/foo.py",
    "src/backtesting/engine.py",
    # PLURAL collision class — the original defect one character over. All five
    # are ordinary English words that end in the plural test token.
    "src/backtests/engine.py",
    "app/contests/views.py",
    "app/protests/views.py",
    "src/roam/attests/emit.py",
    "releases/latests/index.js",
    # Vendored C++ doctest framework and vitest tooling: "doc"/"vi" are
    # deliberately absent from the qualifier allowlist.
    "third_party/doctest/doctest.h",
    "node_modules/vitest/dist/index.js",
    # The filename half, anchored. These merely CONTAIN a test token.
    "src/roam/commands/cmd_test_gaps.py",
    "src/roam/commands/cmd_test_impact.py",
    "src/roam/commands/cmd_test_pyramid.py",
    "src/roam/commands/cmd_test_scaffold.py",
    "src/roam/commands/cmd_test_hermeticity.py",
    "src/roam/commands/cmd_pytest_fixtures.py",
    "src/roam/index/pytest_fixtures.py",
    "src/utils/latest_snapshot.py",
    "src/routing/fastest_path.py",
]


@pytest.mark.parametrize("path", MUST_BE_FALSE)
def test_w1512_production_source_is_not_a_test_path(path: str) -> None:
    """A shipped package that merely NAMES testing is production source.

    Every one of these is suppressed at ~123 call lines across 35 modules when
    wrongly True: auth-gaps discards HIGH missing-authz findings, rules/dataflow
    drops the file from taint analysis (sinks include ``eval``, ``os.system``,
    ``pickle.loads``, ``yaml.load``), safe-delete reports non_test_callers == 0
    and verdict SAFE on live referenced code, verify drops checks and still
    prints PASS, and attest cryptographically signs the inflated number.
    """
    from roam.catalog._shared import is_test_path

    assert is_test_file(path) is False, f"{path!r} is production source, not a test path"
    assert is_test_path(path) is False, f"catalog ``is_test_path`` must agree with commands on {path!r}"


# ---------------------------------------------------------------------------
# Corpus, direction 2 — MUST be True.
# ---------------------------------------------------------------------------

MUST_BE_TRUE = [
    # LLVM / Chromium convention.
    "unittests/foo.py",
    "unittest/foo.py",
    "llvm/unittests/Support/DynamicLibrary/PipSqueak.cpp",
    "llvm/unittests/ADT/APIntTest.cpp",
    # MongoDB's entire JS correctness suite.
    "jstests/x.js",
    "jstests/core/index_bounds.js",
    # The rest of the glued-qualifier family.
    "apitests/x.py",
    "smoketests/x.py",
    "e2etests/x.js",
    "regressiontests/x.py",
    "acceptancetests/x.rb",
    "systemtest/x.java",
    # Delimiter-glued __tests__ with a project prefix.
    "foo__tests__/a.js",
    # Vitest type-level tests: the canonical detector returns False on these,
    # so the anchored filename rule is the only thing covering them.
    "packages-private/dts-test/appDirective.test-d.ts",
    "packages-private/dts-test/tsx.test-d.tsx",
]


@pytest.mark.parametrize("path", MUST_BE_TRUE)
def test_w1512_genuine_test_conventions_classify_as_tests(path: str) -> None:
    """A qualified test token IS a test directory, glued or not.

    ``unittests`` and ``backtests`` carry the identical token; only the
    qualifier separates them. Dropping this half turns ``unittests/``,
    ``jstests/`` and ``apitests/`` into production source at ~123 suppression
    sites, which is exactly the failure the naive exact-component fix produces.
    """
    from roam.catalog._shared import is_test_path

    assert is_test_file(path) is True, f"{path!r} is a genuine test-directory convention"
    assert is_test_path(path) is True, f"catalog ``is_test_path`` must agree with commands on {path!r}"


# ---------------------------------------------------------------------------
# Accepted sacrifice, pinned so it is a DECISION and not a silent gap.
# ---------------------------------------------------------------------------

ACCEPTED_FALSE_NEGATIVES = [
    # No boundary-free lexical rule separates ``argparsetest`` from
    # ``greatest``: both are [a-z]+test with zero orthographic signal. A length
    # heuristic fails — the longest false prefix (grea, back) is 4 and the
    # shortest true prefix (unit) is 4, and cutting at >=5 admits the
    # productive ``-est`` superlative class. Both files were read:
    # argparsetest.go defines ``func TestArgParsing[T any]`` (generic — ``go
    # test`` will not run it) and jsonfieldstest.go defines
    # ``ExpectCommandToSupportJSONFields`` with zero ``func Test``. They are
    # exported helper libraries. Being production here is the SAFE error: they
    # get MORE analysis (noise a reviewer dismisses), never a suppressed
    # finding. Both are .go, so the .py-gated test-selection branches in
    # cmd_verify and cmd_affected_tests are untouched.
    "pkg/jsonfieldstest/jsonfieldstest.go",
    "pkg/cmd/issue/argparsetest/argparsetest.go",
]


@pytest.mark.parametrize("path", ACCEPTED_FALSE_NEGATIVES)
def test_w1512_accepted_false_negatives_are_a_decision_not_an_accident(path: str) -> None:
    """These stay production ON PURPOSE. Changing them requires new evidence.

    A test that pins the sacrifice is the difference between a documented cost
    and a hole nobody remembers choosing. If a future change makes one of these
    True, this test fails and the author must justify the qualifier that did it
    — because the same qualifier is what would let ``greatest`` or
    ``backtests`` back in.
    """
    assert is_test_file(path) is False, (
        f"{path!r} is a KNOWN, accepted false negative — see the comment above. "
        "If you made it True, verify you did not also admit 'greatest'/'backtests'."
    )


def test_w1512_accepted_false_positive_delimited_back_tests() -> None:
    """``back-tests/`` is wrongly SUPPRESSED, and that is the one accepted cost
    on the dangerous side of the ledger.

    A delimiter- or camel-separated plural (``last == "tests"``) is accepted
    unconditionally, because that is what makes ``foo__tests__``,
    ``integration-tests`` and ``MyAppUITests`` work. The quant convention
    ``back-tests/`` (a backtesting engine, production code) is caught by the
    same clause. Requiring a qualifier here instead would drop the entire
    ``<project>-tests`` / ``<Project>.Tests`` family, which is a far larger and
    more common class.

    MEASURED: every candidate rule considered for W1512 — including the W1511
    commit this replaces, and the pre-W1511 substring rule — classifies
    ``src/back-tests/engine.py`` as a test path. It is a pre-existing miss, not
    a regression introduced here, and it is pinned so the next reader knows it
    was seen rather than missed.
    """
    assert is_test_file("src/back-tests/engine.py") is True, (
        "back-tests/ became False — that is an improvement, but it means the unconditional "
        "delimited-plural clause changed; re-verify foo__tests__/, integration-tests/ and MyAppUITests/"
    )
    # The glued spelling, which is the one that actually matters for the
    # attest/attests class, stays production.
    assert is_test_file("src/backtests/engine.py") is False


# ---------------------------------------------------------------------------
# The component predicate, including the case-consistency the W1511 commit
# contradicted within its own pin table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        # Glued qualifier + token.
        ("unittests", True),
        ("unittest", True),
        ("jstests", True),
        ("apitests", True),
        ("smoketests", True),
        ("e2etests", True),
        ("regressiontests", True),
        ("acceptancetests", True),
        ("systemtest", True),
        ("perftests", True),
        ("securitytests", True),
        ("foo__tests__", True),
        # Plural collision class — the whole point of pinning day one.
        ("backtests", False),
        ("contests", False),
        ("protests", False),
        ("attests", False),
        ("latests", False),
        # Singular collision class.
        ("latest", False),
        ("greatest", False),
        ("contest", False),
        ("protest", False),
        ("attest", False),
        ("pytest", False),
        ("doctest", False),
        ("vitest", False),
        # ``Testing`` as a namespace segment vs ``testing``/``TESTING`` as a
        # directory. W1511 returned True for ``backTesting`` while asserting
        # ``backtesting`` and ``Backtesting`` were False — three spellings of
        # one word, two answers.
        ("testing", True),
        ("TESTING", True),
        ("Testing", False),
        ("SupportTesting", False),
        ("backTesting", False),
        ("backtesting", False),
        ("Backtesting", False),
        ("integrationTesting", True),
        # "spec" only via the camelCase RSpec target shape.
        ("UserSpec", True),
        ("image-spec", False),
        ("openapi-spec", False),
        ("specs-go", False),
        ("myspec", False),
        # Shipped helper crates / packages named <thing>-test.
        ("tokio-test", False),
        ("runtime-test", False),
        ("dts-test", False),
    ],
)
def test_w1512_component_predicate(component: str, expected: bool) -> None:
    assert _is_test_dir_component(component) is expected, f"_is_test_dir_component({component!r}) should be {expected}"


# ---------------------------------------------------------------------------
# PROPERTY TEST — generated inputs, not a hand-written list.
#
# W1511's Direction-3 test asserted a strict-subset property by iterating the
# same fixed path list the other two directions used. That list contained no
# control characters, so it could not observe that ``_TEST_DIR_CAMEL`` was
# anchored with ``$`` (matches before a trailing newline) rather than ``\Z``.
# The generator below produces the inputs a fixed list cannot.
# ---------------------------------------------------------------------------

_SEED = 20260806  # deterministic; bump only with a stated reason
_TOKENS = ("test", "tests", "Test", "Tests", "spec", "Spec", "testing", "Testing", "__tests__")
_QUALIFIED = ("unit", "api", "integration", "smoke", "e2e", "android", "shared", "js")
_UNQUALIFIED = ("la", "at", "con", "pro", "grea", "back", "pre", "re", "my", "py", "doc", "vi")
_CONTROL_SUFFIXES = ("\n", "\r", "\r\n", "\t", "\x00", "\x0b", "\x0c", "\x1f", "\x7f")
_SEPARATORS = ("", "-", "_", ".", " ")
_ALPHABET = string.ascii_letters + string.digits + "-_. " + "".join(_CONTROL_SUFFIXES)


def _generate_components(n: int) -> list[str]:
    """Three strata: token+control-suffix, prefix+token, pure-random alphabet."""
    rng = random.Random(_SEED)
    out: list[str] = []
    for _ in range(n // 3):
        # Stratum 1 — a component that WOULD match, wearing a control suffix.
        base = rng.choice(_QUALIFIED) + rng.choice(_SEPARATORS) + rng.choice(_TOKENS)
        out.append(base + rng.choice(_CONTROL_SUFFIXES))
    for _ in range(n // 3):
        # Stratum 2 — random prefix + token, with and without a separator.
        prefix = rng.choice(_QUALIFIED + _UNQUALIFIED)
        out.append(prefix + rng.choice(_SEPARATORS) + rng.choice(_TOKENS))
    while len(out) < n:
        # Stratum 3 — pure random over an alphabet that INCLUDES control chars.
        out.append("".join(rng.choice(_ALPHABET) for _ in range(rng.randint(1, 18))))
    return out


_GENERATED = _generate_components(200_000)


def test_w1512_property_control_characters_are_never_a_test_directory() -> None:
    """PROPERTY: for every component c, any control char in c implies f(c) is False.

    A real path component produced by ``splitlines()`` or by the index DB can
    never contain ``\\n``, ``\\r``, ``\\x00`` or ``\\x7f``. A predicate that
    answers True for one is answering about a string that is not a directory
    name, and it is doing so because a regex was anchored with ``$`` instead of
    ``\\Z``. This property is what a fixed path list cannot check: it requires
    inputs nobody would think to write down.

    Stated as a law rather than a case list so that ANY future re-introduction
    of ``$``-anchoring — in this predicate or in a helper it grows — is caught
    on the first run, not after the next adversarial review.
    """
    violations = [
        c for c in _GENERATED if any(ch in c for ch in "\x00\t\n\x0b\x0c\r\x1f\x7f") and _is_test_dir_component(c)
    ]
    assert not violations, (
        f"{len(violations)} generated components containing a control character were classified as test "
        f"directories; first 5: {violations[:5]!r}. This is the $-vs-\\Z anchor defect."
    )


_GENERATED_BASENAMES = [
    stem + ext
    for stem in ("test_a", "foo_test", "foo.test", "foo.spec", "x.test-d", "latest_snapshot")
    for ext in (".py", ".go", ".js", ".ts", ".tsx")
] + [
    stem + ext + suffix
    for stem in ("test_a", "foo_test", "foo.test", "foo.spec", "x.test-d")
    for ext in (".py", ".go", ".js", ".ts")
    for suffix in _CONTROL_SUFFIXES
]


def test_w1512_property_control_characters_are_never_a_test_basename() -> None:
    """PROPERTY: for every basename b, any control char in b implies f(b) is False.

    The SAME law as the directory half, asserted on the filename half, because
    the filename regex grafted into this rule anchors with ``$`` and its
    character classes (``[^.]``) match a newline — so ``"foo_test.go\\n"``
    matches whether the anchor is ``$`` or ``\\Z``. Rewriting the anchor does
    not close it; rejecting control characters before any regex runs does. This
    property is what makes the module's claim ("no regex in here can be fooled
    by a trailing newline") true of BOTH halves instead of only the one the
    original adversarial review happened to look at.

    Strictly narrowing: every name rejected here was accepted by the pre-W1512
    substring rule as well, so nothing that used to be a test file stops being
    one for any input a real filesystem can produce.
    """
    # Imported inside the test, not at module scope, so that running this file
    # against the pre-fix commit yields informative per-case failures instead of
    # one collection-time ImportError that hides all 94 other assertions.
    from roam.commands.changed_files import _is_test_basename

    violations = [
        b for b in _GENERATED_BASENAMES if any(ch in b for ch in "\x00\t\n\x0b\x0c\r\x1f\x7f") and _is_test_basename(b)
    ]
    assert not violations, (
        f"{len(violations)} generated basenames containing a control character were classified as test "
        f"files; first 5: {violations[:5]!r}. This is the $-vs-\\Z anchor defect on the filename half."
    )
    # …and the clean spellings of the same names are still True, so the guard
    # narrowed nothing a real path can hit.
    assert _is_test_basename("foo_test.go") is True
    assert _is_test_basename("test_a.py") is True
    assert _is_test_basename("x.test-d.ts") is True
    assert _is_test_basename("latest_snapshot.py") is False


def test_w1512_property_the_predicate_is_deterministic() -> None:
    """PROPERTY: f(c) depends only on c — same input, same answer, always.

    The predicate feeds ~123 suppression call lines across 35 modules. If it
    were order- or state-dependent (a cache keyed on something other than the
    component, a mutable allowlist), a file could be analysed in one run and
    silently skipped in the next with no diff to explain it. The debug
    enumeration added alongside this rule mutates module state on every call,
    which is precisely why this law is pinned.
    """
    first = [_is_test_dir_component(c) for c in _GENERATED]
    second = [_is_test_dir_component(c) for c in reversed(_GENERATED)]
    second.reverse()
    assert first == second, "predicate answer changed with call order — it is not a pure function of its input"


def test_w1512_property_plural_collisions_stay_production_under_every_case() -> None:
    """PROPERTY: <english-stem> + <test token>, glued, is never a test directory.

    ``attests`` is the plural of the very package (``src/roam/attest/``) whose
    misclassification motivated this whole line of fixes; a rule that fixes
    ``attest`` and admits ``attests`` has moved the bug one character, not
    removed it. Generated across case variants because the singular/plural pair
    is exactly where a hand-written list stops after one spelling.
    """
    stems = ("back", "con", "pro", "la", "grea", "pre", "re", "my", "at")
    failures = []
    for stem in stems:
        for tok in ("test", "tests", "testing"):
            for variant in (
                stem + tok,
                (stem + tok).upper(),
                (stem + tok).capitalize(),
                stem.capitalize() + tok,
            ):
                if _is_test_dir_component(variant):
                    failures.append(variant)
    assert not failures, (
        f"glued english-stem + test-token components classified as test directories: {failures!r}. "
        "Each one silently suppresses every finding in that directory."
    )


def test_w1512_property_generated_positives_carry_a_qualifier_or_a_boundary() -> None:
    """PROPERTY: f(c) True implies c has an orthographic boundary or a qualified prefix.

    The rule is allowed to say True for only two reasons: the component is
    segmented (a delimiter or a camelCase boundary separates the test token
    from what precedes it), or it is glued but the prefix is on the explicit
    qualifier allowlist. Any True with neither justification is a rule that has
    started guessing, which is how ``latest/`` was classified as a test
    directory for the entire life of the substring version.
    """
    from roam.commands.changed_files import _EXACT, _QUALIFIERS, _segments

    unjustified = []
    for c in _GENERATED:
        if not _is_test_dir_component(c):
            continue
        segs = _segments(c)
        if len(segs) > 1:
            continue  # segmented: a real boundary exists
        s = segs[0]
        if s in _EXACT or s == "testing":
            continue  # the component IS the token
        if any(s.endswith(t) and s[: -len(t)] in _QUALIFIERS for t in ("tests", "test")):
            continue  # glued, but explicitly qualified
        unjustified.append(c)
    assert not unjustified, (
        f"{len(unjustified)} components were True with neither a boundary nor a qualifier; first 5: {unjustified[:5]!r}"
    )
