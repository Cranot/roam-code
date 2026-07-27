"""Single source of truth for the anti-leak internal-language pattern catalogue.

Stdlib-only (imports ``re``, ``hashlib``, ``os``, ``functools`` — NO pytest,
NO ``roam`` imports). This module is imported by BOTH the CI gate
(``tests/test_no_internal_language.py``) AND the commit/push-time hook CLI
(``scripts/scan_internal_language.py``), so the forbidden-pattern
definitions live in exactly ONE place.

Root cause this addresses: the anti-leak gate previously lived only in the
pytest suite, which ran in CI. With no installed git hook, leaks reached the
PUBLIC repo before CI caught them. Extracting the catalogue here lets a
stdlib-only hook scan staged changes at commit time and the full tree at push
time, with no third-party dependency and no ``roam`` index build.

Two catalogues, two techniques
-------------------------------
``FORBIDDEN_PATTERNS`` holds regex SHAPES: dated filenames, ticket-ID
conventions, session-marker templates. A shape leaks nothing by existing in
the pattern source — matching ``dev/SPRINT-2026-05-01.md`` as a regex
doesn't reveal any private fact about this project, so these stay plain
``re.Pattern`` objects.

A handful of entries are different in kind: they exist to catch a specific
REAL VALUE — a personal filesystem path, a client's name, private domain
identifiers, a fork contributor's handle, the name of the platform this
project is developed alongside. Writing those as plain regexes would defeat
the entire purpose of a catalogue that itself ships in the public repo: the
denylist would BE the leak, readable by anyone who opens this file. Those
entries are stored as salted SHA-256 digests of their normalized form
instead — see "Hashed literal terms" below — and matched by tokenizing each
scanned line and hashing candidate word n-grams, rather than by regex.

Hashed literal terms — what this buys you, honestly
-----------------------------------------------------
This is obfuscation, not cryptography. The salt below (``_PUBLIC_SALT``) is
committed to this same public repository — it has to be, so the committed
digests keep working standalone with no private file present, which is the
normal case for every public clone and for CI. Because the salt is public
and the set of plausible plaintexts here is a short wordlist (a person's
name, a handful of domain terms) rather than a real keyspace, a targeted
attacker who already suspects a specific value can hash their guess with the
committed salt and confirm it in milliseconds. Hashing does NOT stop that.

What it does stop is casual discovery: a plain ``grep``, a GitHub code
search, or someone idly reading this file and finding the target values
printed on the page. That is the realistic threat a catalogue like this one
faces (it must ship in the open to do its job), and closing off casual
discovery — while being upfront that it's all this achieves — is the goal
of the hashing below.

Optional private supplement
----------------------------
A maintainer's checkout may keep a gitignored ``scripts/
internal_language_literals.txt`` (``label<TAB>literal`` per line) with
additional real values to catch locally — never committed. When present, its
entries are hashed and merged into the runtime lookup, using
``ROAM_LEAK_SALT`` from the environment if set, else the same committed
public salt. When the file and env var are both absent (the public/CI case),
the committed hashes still work standalone. Regenerate the committed digests
from that file with ``scripts/regen_leak_hashes.py``.

Whitelisted contexts (intentional uses):
- The CI test file itself (it owns the patterns it forbids).
- ``src/roam/security/aibom_extension.py`` and the ``test_ai_ratio.py`` /
  ``test_v12_2.py`` test fixtures: they describe and detect AI-authorship
  trailers as a product feature, not as a session signature.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
from pathlib import Path

# Path-loaded consumers access this module dynamically; keep the public
# catalogue contract explicit for static export audits.
__all__ = (
    "EXCLUDED_DIRS",
    "FORBIDDEN_PATTERNS",
    "SCAN_EXTENSIONS",
    "WHITELIST_FILES",
    "scan_text",
    "should_scan",
)

# ---------------------------------------------------------------------------
# Pattern definitions — regex SHAPES only. Literal-value entries live in the
# hashed-term catalogue further down; see the module docstring.
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Session-pass numbering ("Pass 79 — deprecated commands")
    ("Pass NN session marker", re.compile(r"\bPass \d+ — ")),
    # Letter-coded session markers ("R5 (2026-05-07) — ", "X14 (2026-05-06):")
    ("Letter-coded session marker", re.compile(r"\b[A-Z]{1,2}\d+ \(\d{4}-\d{2}-\d{2}\)")),
    # "(round 4 #15)" / "(round 3 #2 noted that)"
    ("Round-numbered session marker", re.compile(r"\(round \d+ #\d+")),
    # "Phase 0/1 of v2 monetization plan"
    ("v2 monetization plan reference", re.compile(r"Phase \d+(?:\.\d+)? of (?:the )?v2 monetization plan")),
    # "(per build_priorities.md)" / "(per internal backlog)"
    (
        "Internal-doc cross-reference",
        re.compile(r"\(per (?:build_priorities\.md|dev/CODE-BACKLOG\.md|the v\d+ plan)\)"),
    ),
    # "monetization_v2_subscription_pivot.md" filename references
    ("Monetization v2 strategy filename", re.compile(r"\bmonetization_v2_subscription_pivot\.md\b")),
    # "dogfood notes 2026-05-XX" / "dogfood R17 2026-05-01"
    (
        "Dogfood-notes session marker",
        re.compile(r"\bdogfood notes \d{4}-\d{2}-\d{2}\b|\bdogfood R\d+ \d{4}-\d{2}-\d{2}"),
    ),
    # Dated dogfood markers in ANY adjacency form: "(2026-05-XX dogfood)",
    # "(2026-06-10 dogfood: ...)", "dogfood 2026-05-04 —", "2026-06-07
    # dogfood:". The original paren-only pattern missed every variant with
    # trailing text; the separator-gap form catches them while leaving
    # "internal/dogfood/<FILE>-<date>.md" path mentions to the memo-filename
    # pattern below (letters in the gap don't match).
    (
        "Dated dogfood parenthetical",
        re.compile(r"\d{4}-\d{2}-\d{2}[ ,:;)]{0,3}dogfood\b|\bdogfood\b[ ,:;(]{0,3}\d{4}-\d{2}-\d{2}"),
    ),
    # Internal session reports
    (
        "Internal session report filename",
        re.compile(
            r"\bOVERNIGHT-\d{4}-\d{2}-\d{2}\.md\b|\bDOGFOOD-RESULTS-\d{4}-\d{2}-\d{2}\.md\b|"
            r"\bREPORT-\d{4}-\d{2}-\d{2}(?:-round\d+)?\.md\b|\bRELEASE-CHECKLIST\.md\b"
        ),
    ),
    # Old GitHub Pages docs URL (we migrated to roam-code.com/docs/)
    ("Old GH Pages docs URL", re.compile(r"https?://cranot\.github\.io/roam-code/")),
    # CFO-objection sales-pitch script
    (
        "CFO-objection script",
        re.compile(
            r"signed PO by Friday|highest-conversion buyer-meeting|Article-12-curious leads|"
            r"Hosted-product Phase 0 helper"
        ),
    ),
    # Monetization-v2 phrasing leftovers
    ("Monetization-v2 leftover", re.compile(r"\bv2-monetization\b|\bv2 monetization layer\b")),
    # Stripe Atlas / Greek IKE corporate-structure decisions in the wrong place
    (
        "Corporate-structure decision leak",
        re.compile(r"Stripe Atlas Delaware C-corp / Greek freelancer|Greek IKE vs Atlas"),
    ),
    # Internal-roadmap phrasing that crept into shipped module docstrings
    # ("deferred from MVP", "deferred to phase 2", "(future)"). Customers
    # don't need to know our internal sequencing.
    (
        "Internal-roadmap phrasing in shipped docs",
        re.compile(r"\bdeferred from MVP\b|\bdeferred to (phase|wave|sprint)\b", re.IGNORECASE),
    ),
    # Sales / strategy positioning words that have meaning in our internal
    # docs but make customer-facing comments read like a strategy memo.
    # "buyer wedge" / "wedge identified by …" / "first dollar" /
    # "closes Roam Review deals" — all collected from real leaks.
    (
        "Sales-positioning shorthand",
        re.compile(
            r"\bbuyer wedge\b|wedge identified by|"
            r"\bfirst dollar\b|closes Roam Review deals|"
            r"\bproduct agent\b",
            re.IGNORECASE,
        ),
    ),
    # Internal-pricing-doc cross-references in shipped files.
    # ``Per pricing_v3 build priorities``, ``per pricing_v4 P2``, etc.
    (
        "Pricing-doc cross-reference",
        re.compile(r"pricing_v\d+ build priorities|pricing_v\d+ P\d+", re.IGNORECASE),
    ),
    # Phasing of unrelated-to-this-file design work in module docstrings.
    # ``Phase 1 of the daemon design``, ``Phase 2 of the agent rollout``.
    # Sequencing belongs in commits / planning docs, not shipped code.
    (
        "Phase-of-design module docstring",
        re.compile(r"Phase \d+ of (?:the )?[a-z][a-z\- ]+ (?:design|rollout|plan)", re.IGNORECASE),
    ),
    # Date-stamped internal memo filenames in dev/ (catch-all).
    # Public dev/ docs are NOT date-suffixed (only MCP-SECURITY-POSTURE.md,
    # the example-plugin subtree, and dev scripts). Anything matching
    # ``dev/<ALLCAPS>-YYYY-MM-DD.md`` is session-cadence planning content
    # that belongs under ``internal/planning/``.
    (
        "Date-stamped dev/ memo filename",
        re.compile(r"\bdev/[A-Z][A-Z0-9_-]*-\d{4}-\d{2}-\d{2}[A-Za-z0-9_-]*\.md\b"),
    ),
    # Internal-planning-doc dev/ filenames that exist without date suffix
    # but are still session/strategy-cadence rather than user-facing.
    (
        "Internal-planning dev/ filename",
        re.compile(
            r"\bdev/(?:ROAM-STRATEGY|NEXT-BUILD-PRIORITIES|DOCS-CLEANUP-PLAN|"
            r"SESSION-HANDOVER|MCP-EVOLUTION|MCP-SERVER-CARD|MCP-TASKS-EVAL|"
            r"MCP-ELICITATION-CANDIDATES|DETECTOR-FP-METHODOLOGY|"
            r"OWASP-TAINT-RULE-PACK-RESEARCH|CROSSWALK-ADDITIONS|"
            r"PERF-PHASES|ROADMAP|BACKLOG|ARCHITECTURE-FUTURES|"
            r"D[0-9]-[A-Z][A-Z0-9_-]+-SPIKE|MONETIZATION-OPPORTUNITIES|"
            r"NEXT-PRIORITIES|V\d+\.\d+-RELEASE-READINESS|"
            r"SPRINT-\d{4})\b"
        ),
    ),
    # "cash path" / "revenue path" — internal revenue framing.
    (
        "Cash-path framing",
        re.compile(r"\brevenue path\b|\bthe (?:current )?cash path\b", re.IGNORECASE),
    ),
    # Internal/ folder revenue-ops cross-references from shipped code.
    # ``internal/dogfood/`` and ``internal/smoke/`` references are
    # legitimate (dogfood corpus is cited extensively in AGENTS.md; smoke
    # is the output path of dev/roam_smoke.py). Block only the revenue-ops
    # / planning cross-refs.
    (
        "Internal/ folder revenue-ops or planning cross-reference",
        re.compile(r"\binternal/(?:pr-replay-engagement-playbook|planning/[A-Z])"),
    ),
    # Date-stamped ALLCAPS memo filenames ANYWHERE (generalizes the dev/-
    # scoped pattern above). Session-cadence memos follow the
    # ``<TOPIC>-YYYY-MM-DD[-slug].md`` convention; naming one from a shipped
    # docstring points readers at a private file and leaks the cadence.
    # Cite the content neutrally ("the dogfood synthesis notes") instead.
    (
        "Dated internal memo filename",
        re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{4}-\d{2}-\d{2}[A-Za-z0-9-]*\.md\b"),
    ),
    # Claude-memory slug references in shipped code/docs. Memory names are
    # session-private; describing the decision ("per the Roam Guard pivot")
    # carries the same information without naming the memory system.
    (
        "Claude-memory slug reference",
        re.compile(
            r"\bproject_pivot_to_roam_guard\b|\bproject_all_levers_breakthrough\b|"
            r"\bproject_deep_levers_inventory\b|\bproject_x3_haiku_l1_breakthrough\b|"
            r"\[\[(?:project|feedback|reference|user)_[a-z0-9_]+\]\]"
        ),
    ),
    # Absolute VPS filesystem paths — leak the deployment box's layout.
    # Tracked configs must use PATH-resolved commands / relative paths.
    (
        "VPS absolute path",
        re.compile(r"/root/(?:apps|services|repos|legacy|accounting-source)/"),
    ),
]


# ---------------------------------------------------------------------------
# Hashed literal terms — see the module docstring ("Hashed literal terms —
# what this buys you, honestly") before touching anything below.
# ---------------------------------------------------------------------------
#
# Each entry is (generic_label, sha256_hex_of_normalized_phrase, token_count).
# Regenerate with ``python scripts/regen_leak_hashes.py`` from a local,
# gitignored ``scripts/internal_language_literals.txt`` — never hand-paste a
# digest without going through that script, or the normalization and the
# stored hash can silently drift apart.

_PUBLIC_SALT = "roam-code-anti-leak-public-salt-v1"
_PRIVATE_SALT_ENVVAR = "ROAM_LEAK_SALT"
_PRIVATE_LITERALS_FILENAME = "internal_language_literals.txt"

# 7 of these 12 labels came from an ORIGINAL regex with no ``re.IGNORECASE``
# — the case sensitivity was load-bearing, not incidental: e.g. the private
# platform name is a common-enough word that this project's own codebase
# legitimately reuses it in lowercase (env-var names, vendored-path
# comments) as an intentional, already-public naming convention, while a
# capitalized mid-sentence mention is the real leak shape. Casefolding
# everything uniformly would catch the former and break real, shipped,
# non-leak code. So case sensitivity is tracked per label and preserved
# exactly as each original regex had it; the other 5 labels genuinely were
# ``re.IGNORECASE`` and stay case-insensitive.
_CASE_SENSITIVE_LABELS = frozenset(
    {
        "personal-machine-path",
        "internal-domain-abbreviation",
        "session-memory-path",
        "internal-policy-clause",
        "legacy-identity-string",
        "unlisted-contributor-handle",
        "private-platform-name",
    }
)

_COMMITTED_HASHED_TERMS: tuple[tuple[str, str, int], ...] = (
    # A personal machine's filesystem path (two drive-letter variants).
    ("personal-machine-path", "ad2edab6d0d101a93e515af6aad1b0336ba5562bc34b3c8f9e46654ebdec2ffd", 3),
    ("personal-machine-path", "e263598c28f17abc0b080b7a4bf0b36ff208d58d9553cf6b4e69f4ab2981c8f6", 3),
    # A day-job client's codename (two spellings).
    ("internal-project-codename", "e44d93d135f2c759b6e31ee0a47d76c8735f583783c65f1f651c0ad1cf48f497", 2),
    ("internal-project-codename", "645fc1c1e38e08a70205a9f26aabb02dab76ae9b70906d812ff13944f17734ab", 2),
    # Private domain identifiers from that same engagement (4 distinct
    # terms, one of them also as a fused camelCase identifier).
    ("internal-domain-term-a", "fefa6e1703316473fa856d73077ec3d8ec162526f620d6b8e6b73128e5f5d330", 1),
    ("internal-domain-term-a", "cf1d3a8ad38855727030c73be2d1b279dfebf2c710381f7cc02d3f078df935d3", 1),
    ("internal-domain-term-b", "01ed4091b80592cd3bf72676e904c7df2690ea3ebe3676437e5c0e523fe401c0", 1),
    ("internal-domain-term-c", "eb623687d5ed9d1cddb08b4e763cf71e859198d40000081e3ca7de895d1d15f2", 1),
    ("internal-domain-term-c", "7c0b226c2eb2450880edba8bbfdf9463ce9ec0763af8dac66de4c4d7dc82157e", 1),
    ("internal-domain-term-d", "5648cd2e47f0ccc5b7331292ec4955b88e10217afd9fa771191854b355314873", 1),
    # A short standalone domain abbreviation. Compound identifiers that
    # merely CONTAIN it (``FOO_xyz``, ``xyz-FOO``, ``FOO.xyz``) are exempt —
    # see ``_ADJACENCY_EXEMPT_HASHES`` below, matching the old regex's
    # negative lookahead.
    ("internal-domain-abbreviation", "a9514debc25162e75415f124ccd695f31392746382852876796f01d0ebd04b4b", 1),
    # A session-memory tool's local storage path.
    ("session-memory-path", "4c3cf51534e337330a52ecd7dab800a4da74f2fa3fdcd011103f9d5bd798c2c0", 5),
    # An internal conflict-of-interest / vendor-exclusion policy clause
    # (three phrasings).
    ("internal-policy-clause", "2001880709a12531d5e29344a121476dd17dc68762ceddb472a889412abd5cc2", 5),
    ("internal-policy-clause", "361dd23e67129fe3ce73937414ec21c637db7259ff003da4cf41a3c60cd63d67", 3),
    ("internal-policy-clause", "2cd22c3d895eeb9ddc357305d6a2cc0a208e21f108ed3da53cd4b486289e32f8", 2),
    # A retired git-config identity string that leaked local filesystem
    # layout via commit metadata.
    ("legacy-identity-string", "4ae6f4452921bc6288c764520da8d13bec8b0980b9fbbe06ea08cc066033bdda", 1),
    # A fork contributor's handle, credited via LICENSE/CONTRIBUTING rather
    # than by name in shipped code (four phrasings: two @-mentions, two
    # "credit <name>" phrases).
    ("unlisted-contributor-handle", "24150a7c3a84974df191a5320b1b8029cb2790a1fad68e0b945de06f7bbd0af6", 1),
    ("unlisted-contributor-handle", "f8a42d93a56bd061eebc93964eb86741d83e94176e0efa0a266da2e4e383e675", 2),
    ("unlisted-contributor-handle", "839b9335924d19112d5e1316f3a4e4d75e8025d4f3b87a9d56e42b196795eb54", 3),
    ("unlisted-contributor-handle", "5a57536e1518ad4a530065b2b50db036816e7e2f0ccc583aec1e75b553f4798f", 3),
    # The private name of the platform this project is developed alongside.
    # Case-sensitive: this repo also legitimately reuses the same word,
    # lowercase, in env-var and vendored-path conventions (see
    # _CASE_SENSITIVE_LABELS above) — only a capitalized mention is the leak.
    ("private-platform-name", "82fedeef1be283818b6b249fc77ac0c18c296cf0fef5bfa6091ddf55aaa580c3", 1),
)

# Hash of a 1-token candidate's normalized text -> token counts of multi-word
# terms that start with that token. Lets the scanner skip building (and
# hashing) a multi-word window unless its first word could possibly lead
# anywhere, instead of hashing every n-gram at every position. Stored in
# BOTH exact-case and casefolded form for every multi-word term (the scanner
# tries both forms per candidate without knowing a term's case mode ahead
# of time; see _candidate_hashes).
_LEADING_MULTI_HASHES: dict[str, tuple[int, ...]] = {
    "04bf9d36cf2ec5e76f8e3162e843f1ba8b07a7b3a96593e3c98f7f381afe995b": (3,),
    "62f4fb5c094b9523c06a2fa542c942fc775a3830ec527b3006afd30770167312": (3,),
    "64c0375d49cb0614d2b82890e9598b7df336453b47c01f01cf0f7ee28ff4374a": (3,),
    "836571dca59c849313a8e6a2feab568a87c2d91ac02a65acecd215f0638bcb17": (3,),
    "e289ef6041a7026e9c7dce5956e68e286de57c4d2a0df0835a5568fb3b6a22ce": (2, 3),
    "077cd15434d1086db100b32ec2246862877032c8e1bb6301f2eae1e46474dfa4": (2,),
    "a406d9c143e4d88cf7c0599b613750dbc6345bf8d0792531743528154633fbb6": (5,),
    "c01d71f5a62b2b95652e4fc4724ffd11d360bbf191c8a2fd46edd33b8e29d403": (5,),
    "662829684e5cc6112147f0724752a75c9fd35daa8e4f5a33cd4fe5aba4a692d6": (5,),
    "c0d8e9dde81b6f36d6c76bfd8a9f19acf8027a5ab8ef59e21dd0f4ed8c163312": (2, 3),
    "b460c2f5095f66783b578d9cb7ec6762183cadd1650a6dfa2e54040449613b6e": (2,),
    "147cb5f72862985bfe15c77b1baafa936eb0e9f56d81215491b729306351ba54": (3,),
}

# The one standalone-abbreviation hash that gets the "not immediately
# followed by . or -" exemption (matches a compound/placeholder identifier
# use, e.g. ``FOO_xyz``, ``FOO-xyz``, ``FOO.xyz``, none of which are the
# bare standalone term). Underscore never reaches this check at all: the
# raw-token regex below already treats ``_`` as word-forming, so ``FOO_xyz``
# is one token, not two. Exact case only (this term is in
# _CASE_SENSITIVE_LABELS).
_ADJACENCY_EXEMPT_HASHES = frozenset({"a9514debc25162e75415f124ccd695f31392746382852876796f01d0ebd04b4b"})
_ADJACENCY_EXEMPT_NEXT_CHARS = frozenset({".", "-"})

_MAX_NGRAM = 5

# Raw word tokens straight from the line (underscore is word-forming, like
# regex ``\w``). Deliberately does NOT stop at quotes, operators, or other
# punctuation between word-runs — only whitespace/hyphen/slash/backslash
# ever separated a real multi-word target, and letting extraction skip
# straight past everything else means a literal defensively split across a
# string concatenation (two half-strings joined by a ``+`` operator, a
# technique this repo already uses elsewhere to keep a secret out of literal
# source) still gets reassembled and caught. That is a deliberate
# capability, not a bug — see
# tests/test_leak_gate_hashed_terms.py::test_split_literal_across_concatenation_still_detected.
_RAW_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
# Path/hyphen separators folded to spaces before tokenizing for the general
# n-gram check, so a hyphenated codename and its space-separated spelling
# (or a Windows vs. POSIX path) all normalize to the same token sequence.
_SEPARATOR_RE = re.compile(r"[\\/\-]")


def _normalize_tokens(text: str, *, casefold: bool) -> list[str]:
    """Split on path/hyphen separators and punctuation; casefold optionally.

    Collapses whitespace and strips punctuation implicitly: only
    ``[A-Za-z0-9_]+`` runs survive as tokens. Case is preserved when
    ``casefold`` is False, so callers can hash a term in whichever mode
    (case-sensitive or -insensitive) it actually needs — see
    ``_CASE_SENSITIVE_LABELS``.
    """
    collapsed = _SEPARATOR_RE.sub(" ", text)
    raw = _RAW_WORD_RE.findall(collapsed)
    return [t.casefold() for t in raw] if casefold else raw


def _hash_phrase(salt: str, phrase: str) -> str:
    """Hex digest — used for the COMMITTED constants (readable, diffable,
    reproducible in source) and by scripts/regen_leak_hashes.py. The
    scan-time hot path uses ``_hash_phrase_digest`` (raw bytes) instead;
    see the note on ``_candidate_hashes``.
    """
    return hashlib.sha256(f"{salt}:{phrase}".encode("utf-8")).hexdigest()


def _hash_phrase_digest(salt: str, phrase: str) -> bytes:
    """Raw-bytes digest for scan-time comparisons.

    Same SHA-256 value as ``_hash_phrase``, just skipping the hex-string
    encoding step on a path called for nearly every token of every scanned
    line — profiling ``--all`` showed ``hexdigest()`` costing as much as the
    hash computation itself. ``_hash_index()`` converts the committed hex
    constants to bytes once (``bytes.fromhex``) so lookups compare bytes to
    bytes throughout.
    """
    return hashlib.sha256(f"{salt}:{phrase}".encode("utf-8")).digest()


def _candidate_hashes(phrase: str, salts: tuple[str, ...]) -> list[bytes]:
    """Hash *phrase* in both exact-case and casefolded form, every salt.

    The scanner doesn't know ahead of time whether a candidate window might
    match a case-sensitive or case-insensitive committed term, so it tries
    both forms; a phrase with no uppercase letters hashes to the same value
    either way, so the ``set`` dedupes that case for free.
    """
    forms = {phrase, phrase.casefold()}
    return [_hash_phrase_digest(salt, form) for salt in salts for form in forms]


@functools.lru_cache(maxsize=1)
def _active_salts() -> tuple[str, ...]:
    """Salts to hash scan-time candidates against.

    Always the committed public salt (so the committed digests keep working
    standalone). Plus the private salt from ``ROAM_LEAK_SALT``, if set and
    different, so a private supplement hashed under its own salt (see
    ``_load_private_supplement``) can still be matched at scan time. Cached:
    the environment doesn't change mid-scan, and this was measured on the
    hot path (called once per scanned line).
    """
    private_salt = os.environ.get(_PRIVATE_SALT_ENVVAR)
    if private_salt and private_salt != _PUBLIC_SALT:
        return (_PUBLIC_SALT, private_salt)
    return (_PUBLIC_SALT,)


def _load_private_supplement() -> list[tuple[str, str, int, str | None]]:
    """Read the optional gitignored literals file, hash its entries.

    Returns ``[(label, phrase_hash, token_count, leading_hash), ...]``.
    ``leading_hash`` is the hash of just the first token, present only for
    multi-word terms (``None`` for single-word ones) — mirrors
    ``_LEADING_MULTI_HASHES`` for the committed catalogue. Every hash here
    uses ``ROAM_LEAK_SALT`` if set in the environment, else the committed
    public salt. Returns ``[]`` when the file is absent — the normal
    public/CI case.

    A label that coincides with one of the 12 built-in ``_CASE_SENSITIVE_LABELS``
    (e.g. a maintainer testing the built-in catalogue against real values, as
    tests/test_leak_gate_hashed_terms.py does) uses that SAME case mode, so a
    private-file entry can never silently re-broaden a committed
    case-sensitive term by adding a casefolded variant of it to the shared
    lookup. Any other, genuinely new label is casefolded by default.
    """
    path = Path(__file__).with_name(_PRIVATE_LITERALS_FILENAME)
    if not path.is_file():
        return []
    salt = os.environ.get(_PRIVATE_SALT_ENVVAR) or _PUBLIC_SALT
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[tuple[str, str, int, str | None]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "\t" not in line:
            continue
        label, literal = line.split("\t", 1)
        label = label.strip()
        case_sensitive = label in _CASE_SENSITIVE_LABELS
        tokens = _normalize_tokens(literal, casefold=not case_sensitive)
        if not tokens:
            continue
        phrase_hash = _hash_phrase(salt, " ".join(tokens))
        leading_hash = _hash_phrase(salt, tokens[0]) if len(tokens) > 1 else None
        out.append((label, phrase_hash, len(tokens), leading_hash))
    return out


@functools.lru_cache(maxsize=1)
def _hash_index() -> tuple[dict[bytes, str], dict[bytes, tuple[int, ...]], frozenset[bytes]]:
    """Build the merged (committed + optional private) hash lookup, once.

    Returns ``(digest -> label, first-token-digest -> candidate multi-word
    sizes, adjacency-exempt digests)``, all keyed by raw SHA-256 bytes (see
    ``_hash_phrase_digest``) rather than hex strings — this is the hot path
    for ``--all``/``--staged`` scans, and comparing bytes avoids a hex-encode
    on every candidate. Cached for the process lifetime: the private
    supplement file doesn't change mid-scan, and this avoids re-hashing it
    per scanned file.
    """
    lookup: dict[bytes, str] = {bytes.fromhex(h): label for label, h, _size in _COMMITTED_HASHED_TERMS}
    leading: dict[bytes, set[int]] = {bytes.fromhex(h): set(sizes) for h, sizes in _LEADING_MULTI_HASHES.items()}
    exempt = frozenset(bytes.fromhex(h) for h in _ADJACENCY_EXEMPT_HASHES)

    for label, phrase_hash, size, leading_hash in _load_private_supplement():
        lookup.setdefault(bytes.fromhex(phrase_hash), label)
        if leading_hash is not None:
            leading.setdefault(bytes.fromhex(leading_hash), set()).add(size)

    return lookup, {h: tuple(sorted(sizes)) for h, sizes in leading.items()}, exempt


def _first_hashed_match(line: str) -> str | None:
    """Single tokenization pass covering both the adjacency-exempt
    single-token check and the general n-gram check (they used to be two
    separate passes over the line — profiling ``--all`` showed the repeated
    tokenize+hash work costing more than the regex-SHAPE half of scan_text;
    fusing them roughly halves the per-line token/hash work).

    ``_SEPARATOR_RE.sub(" ", line)`` replaces each backslash/slash/hyphen
    with exactly one space — a 1-char-to-1-char substitution — so every
    match's ``.end()`` offset into the COLLAPSED string is also valid
    against the ORIGINAL ``line``, which is what the adjacency-exemption
    check needs (the real next character, before hyphens were folded away).
    """
    lookup, leading, exempt = _hash_index()
    salts = _active_salts()

    collapsed = _SEPARATOR_RE.sub(" ", line)
    matches = list(_RAW_WORD_RE.finditer(collapsed))
    tokens = [m.group(0) for m in matches]
    n = len(tokens)

    for start in range(n):
        token = tokens[start]
        for h in _candidate_hashes(token, salts):
            if h in exempt:
                # Exact case only (the standalone-abbreviation term is in
                # _CASE_SENSITIVE_LABELS); a compound/placeholder identifier
                # immediately following is not a hit.
                next_char = line[matches[start].end() : matches[start].end() + 1]
                if next_char in _ADJACENCY_EXEMPT_NEXT_CHARS:
                    continue
                label = lookup.get(h)
                if label is not None:
                    return label
                continue
            label = lookup.get(h)
            if label is not None:
                return label
            sizes = leading.get(h)
            if not sizes:
                continue
            for size in sizes:
                if size > _MAX_NGRAM or start + size > n:
                    continue
                phrase = " ".join(tokens[start : start + size])
                for h2 in _candidate_hashes(phrase, salts):
                    label2 = lookup.get(h2)
                    if label2 is not None:
                        return label2
    return None


# Files where these patterns are intentional product behaviour or test
# fixtures FOR the patterns themselves — not real leaks.
#
# Customer-facing changelogs are intentionally whitelisted because they
# preserve already-published historical context. New release notes should
# still describe cleanup in neutral terms rather than enumerate private
# pattern values; review and the pushed-history gate cover every non-whitelisted
# public file regardless of extension or directory.
WHITELIST_FILES = {
    # The CI test file owns the pattern catalogue.
    "tests/test_no_internal_language.py",
    # This module is the extracted single-source catalogue (same role).
    "scripts/internal_language_patterns.py",
    # The exemplar ratchet suite: synthetic leak-shaped lines that pin every
    # pattern class so a regex tidy-up can't silently weaken the gate.
    "tests/test_leak_gate_exemplars.py",
    # The phantom-memo detector's regression suite. Tests the matcher
    # that flags backtick-fenced ``dev/<MEMO>-YYYY-MM-DD.md`` paths in
    # CHANGELOG.md; the synthetic fixture has to LITERALLY look like
    # one (e.g. ``dev/NONEXISTENT-2026-05-18.md``) to exercise the
    # regex, otherwise the test silently passes on an empty input.
    "tests/test_changelog_phantoms.py",
    # AI-authorship detector + test fixtures around it.
    "src/roam/security/aibom_extension.py",
    "tests/test_ai_ratio.py",
    "tests/test_v12_2.py",
    # Anchor-slugifier regression suite. ``PFPA_EPIL.IN_PFPA_EPIL-4.DBF``
    # is a real header from the dogfood corpus that broke the slugifier
    # by producing ``pfpaepilinpfpaepil-4dbf``; the test fixtures need
    # the literal underscore-bearing identifier to assert the regression
    # is fixed. Generic replacement names destroy the test signal.
    "tests/test_stale_refs_dogfood_fixes.py",
    # The secret-scan / anti-leak hook's OWN regression suite. It builds
    # synthetic commits, refs, and tree entries containing a hashed literal
    # term (defensively split across a string concatenation — two
    # half-strings joined by a ``+`` operator, specifically so the literal
    # never sits contiguously in this file's OWN tracked source) and asserts
    # that scan_text still finds it in the CONSTRUCTED git metadata. This
    # file's source is the leak gate's own fixture corpus for that assertion
    # and cannot avoid containing the string shapes it tests for — same
    # rationale as the exemplar ratchet suite above.
    "tests/test_secret_scan_hook.py",
    # Public legal template that explains ``AFM`` is the Greek tax-ID
    # abbreviation, with a bracketed placeholder ``[PROVIDER_AFM]`` for
    # the SOW signatory to fill in. The mention is intentional and
    # customer-facing, not a session-context leak.
    "templates/legal/sow-pr-replay.md",
    # Customer-facing changelogs. By definition they document historical
    # state and reference resources that no longer exist (e.g.
    # ``cranot.github.io/roam-code/*`` which 404s post-migration to
    # roam-code.com/docs/). The historical record is the changelog's
    # entire point — rephrasing post-hoc would erase context.
    "CHANGELOG.md",
    "templates/distribution/landing-page/changelog.html",
}


# Glob-style allowlist for paths excluded from the sweep.
EXCLUDED_DIRS = (
    "internal/",
    "reports/",
    "bench-repos/",
    ".roam/",
    "__pycache__",
    ".egg-info",
    "venv/",
    ".venv/",
    "node_modules/",
    "dist/",
    "build/",
    ".git/",
)

# Only check these file extensions.
SCAN_EXTENSIONS = (".py", ".md", ".html", ".yml", ".yaml", ".json", ".txt", ".tmpl", ".css", ".js")


def should_scan(rel_posix_path: str) -> bool:
    """Return True iff a posix relative-path string should be scanned.

    Same logic as the CI test's ``_should_scan``, but takes the already-
    normalised posix relative-path string (e.g. ``"src/roam/cli.py"``) rather
    than a ``pathlib.Path``, so the stdlib-only hook CLI can call it without
    materialising filesystem ``Path`` objects for git-listed entries.
    """
    if rel_posix_path in WHITELIST_FILES:
        return False
    if not rel_posix_path.endswith(SCAN_EXTENSIONS):
        return False
    for excluded in EXCLUDED_DIRS:
        if excluded in rel_posix_path:
            return False
    return True


def scan_text(rel_posix_path: str, text: str) -> list[tuple[str, int, str]]:
    """Scan one file's text for forbidden patterns.

    Returns ``[(pattern_name, line_no, stripped_line[:200])]`` for every hit,
    at most one pattern per line (mirrors the CI test's ``_scan_for_leaks``
    inner loop: the first matching pattern on a line wins, then move on).
    Checks regex SHAPES first, then hashed literal terms (see the module
    docstring) — either can produce the one hit recorded per line.

    The caller is responsible for having decided ``should_scan(rel_posix_path)``
    is True; this function does not re-check.
    """
    hits: list[tuple[str, int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        name = None
        for pattern_name, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(line):
                name = pattern_name
                break
        if name is None:
            name = _first_hashed_match(line)
        if name is not None:
            hits.append((name, line_no, line.strip()[:200]))
    return hits
