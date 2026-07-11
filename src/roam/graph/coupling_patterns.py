"""Expected co-change pattern classification for file pairs.

Some file pairs co-change frequently for *expected* reasons that are not
architectural coupling: parallel translation files (``messages.en.ts`` +
``messages.el.ts``) or sibling docs in a ``docs/<topic>/`` hub. Tagging
those pairs keeps the coupling / dark-matter reports focused on genuine
hidden dependencies.

PRECISION CONTRACT (revise of parked #18): the classifier must have NO
false negatives on genuine cross-concern couplings. The prior attempt
accepted ANY 2-3 lowercase letters as a "locale code", which silently
tagged ``user.api.ts <-> user.db.ts``, ``schema.up.sql <-> schema.dn.sql``,
``bar.io.ts <-> bar.ui.ts`` and ``mod.rs <-> lib.rs`` as expected locale
siblings — false negatives on the dark-matter count/verdict/risk. The
fix is threefold:

1. Locale segments are validated against a REAL allowlist (the ISO-639-1
   two-letter registry, plus common regioned/script forms like ``en-US``,
   ``pt_BR``, ``zh-Hans``) — never a bare ``[a-z]{2,3}`` shape.
2. Both files must share an IDENTICAL stem and differ ONLY by the locale
   segment (same directory, same extension).
3. Known code-concern tokens (``io``, ``ts``, ...) are explicitly denied
   even where they collide with a real ISO-639-1 entry (``io`` = Ido,
   ``ts`` = Tsonga) — on file names those tokens are overwhelmingly
   code concerns, not translations.

Consumers: ``cmd_coupling`` (EXPECTED vs HIDDEN column) and
``roam.graph.dark_matter.dark_matter_edges`` (ADDITIVE ``expected_pattern``
annotation — pairs are never dropped from counts).
"""

from __future__ import annotations

import os
import re

# ISO-639-1 two-letter language codes (full registry). This is the ONLY
# source of truth for "is this segment a locale?" — a bare length/charset
# regex is banned here (see module docstring, precision contract #1).
ISO_639_1_CODES: frozenset[str] = frozenset(
    "aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs "
    "ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es et eu "
    "fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz "
    "ia id ie ig ii ik io is it iu ja jv "
    "ka kg ki kj kk kl km kn ko kr ks ku kv kw ky "
    "la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my "
    "na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu "
    "rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw "
    "ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz "
    "ve vi vo wa wo xh yi yo za zh zu".split()
)

# Code-concern tokens that must NEVER classify as locale codes, even where
# they collide with a real ISO-639-1 entry ("io" = Ido, "ts" = Tsonga,
# "os" = Ossetian). Precision contract #3: on file names these tokens are
# code concerns (API layer, DB layer, migration direction, language
# extensions), and misreading them silences genuine hidden coupling.
CODE_CONCERN_TOKENS: frozenset[str] = frozenset(
    # First row: the verify-failure set. Rest: common language / asset
    # extensions and code-layer tokens.
    "api db io ui up dn rs py ts "
    "js go sh md css sql xml yml ux os cli gui src lib bin obj exe dll "
    "log cfg env tmp bak min dev".split()
)

# Optional region / script subtag after the base code: en-US, pt_BR (2-letter
# region) or zh-Hans / zh-Hant (4-letter script).
_REGION_SUBTAG_RE = re.compile(r"^[A-Za-z]{2}$|^[A-Za-z]{4}$")

_DOC_DIR_TOKENS: frozenset[str] = frozenset({"docs", "doc", "documentation", "guide", "guides", "manual"})
# Doc-hub siblings are Markdown only (narrow, path-anchored — precision
# contract: .rst/.txt/.html siblings under docs/ stay unclassified rather
# than risk hiding genuine coupling).
_DOC_EXTS: frozenset[str] = frozenset({".md"})


def is_locale_code(token: str) -> bool:
    """True when *token* is a real locale code.

    Accepts an ISO-639-1 base (``en``, ``el``) optionally followed by a
    ``-`` or ``_`` separated region/script subtag (``en-US``, ``pt_BR``,
    ``zh-Hans``). Known code-concern tokens are denied outright.
    """
    if not token:
        return False
    parts = re.split(r"[-_]", token, maxsplit=1)
    base = parts[0].lower()
    if base in CODE_CONCERN_TOKENS or base not in ISO_639_1_CODES:
        return False
    if len(parts) == 1:
        return True
    return bool(_REGION_SUBTAG_RE.match(parts[1]))


def classify_pair(path_a: str, path_b: str) -> str:
    """Tag known co-change patterns that aren't architectural coupling.

    Returns one of:

    - ``""`` — no special pattern (the pair stays hidden/normal coupling)
    - ``"expected_locale"`` — sibling translation files: same directory,
      same extension, identical stem, differing ONLY by a validated
      locale segment (``messages.en.ts`` + ``messages.el.ts``, or
      whole-basename codes ``en.ts`` + ``el.ts``)
    - ``"expected_doc_hub"`` — sibling ``.md`` files in the same directory
      under a ``docs/``-anchored path
    """
    a = path_a.replace("\\", "/")
    b = path_b.replace("\\", "/")
    dir_a, base_a = os.path.dirname(a), os.path.basename(a)
    dir_b, base_b = os.path.dirname(b), os.path.basename(b)
    if dir_a != dir_b:
        return ""

    name_a, ext_a = os.path.splitext(base_a)
    name_b, ext_b = os.path.splitext(base_b)
    if ext_a != ext_b:
        return ""

    # Locale shape 1: the whole basename is a validated locale code
    # ("el.ts" + "en.ts"). The stem is empty on both sides, so the names
    # differ only by the locale segment.
    if is_locale_code(name_a) and is_locale_code(name_b):
        return "expected_locale"

    # Locale shape 2: identical non-empty stem + validated locale suffix
    # ("messages.en.ts" + "messages.el.ts", "app.en-US.json" +
    # "app.pt_BR.json"). Works in ANY directory. Precision contract #2:
    # the stems must be byte-identical — "user.api" vs "user.db" shares a
    # stem but "api"/"db" fail the locale allowlist, and "schema.up" vs
    # "schema.dn" fails the same way.
    a_parts = name_a.rsplit(".", 1)
    b_parts = name_b.rsplit(".", 1)
    if (
        len(a_parts) == 2
        and len(b_parts) == 2
        and a_parts[0]
        and a_parts[0] == b_parts[0]
        and is_locale_code(a_parts[1])
        and is_locale_code(b_parts[1])
    ):
        return "expected_locale"

    # Doc-hub: sibling Markdown files in the same directory under a
    # docs/-anchored path (path-anchored, .md only — deliberately narrow).
    if ext_a.lower() in _DOC_EXTS:
        path_parts = dir_a.lower().split("/")
        if any(part in _DOC_DIR_TOKENS for part in path_parts):
            return "expected_doc_hub"

    return ""
