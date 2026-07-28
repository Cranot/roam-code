#!/usr/bin/env python3
"""Numeric-claim drift gate for a repository's GitHub *description*.

The count-drift family already covered by ``dev/build_readme_counts.py`` and
``scripts/sync_surface_counts.py`` stops at the edge of the working tree. The
GitHub repository description — the one-liner rendered above the file list,
in search results, in the org repo list, and in every "awesome-x" scrape that
copies it — lives on GitHub, not in the tree. Nothing in the repo could see
it, so it drifted 43 commands (``238 commands / 224 MCP tools`` against a real
``281 / 244``) with every local gate green.

This script closes that gap in the only form that is honest for prose: a
description is not round-trippable, but **every number in it that also has a
computed truth value is checkable**. So:

* extract ``<number> <unit phrase>`` claims from the live description;
* look each unit up in a caller-supplied map of *computed* truths;
* fail loudly on a value mismatch;
* say so, without failing, when a unit has no computed truth;
* stay silent (exit 0) when the description makes no numeric claim at all.

Nothing here is roam-specific. The truth map is injected, so any repository
can use the same gate by supplying its own provider — see ``--truth-module``.
``dev/description_truth.py`` is this repo's provider (it reuses
``build_readme_counts.collect_counts``; counting is never re-implemented).

Usage::

    # Live check (needs network; GITHUB_TOKEN/GH_TOKEN used when present).
    python dev/repo_description_drift.py \
        --repo Cranot/roam-code --truth-module dev/description_truth.py

    # Offline: check a literal string (used by the tests and for demos).
    python dev/repo_description_drift.py \
        --description "281 commands, 244 MCP tools" --truth "commands=281"

    # Just print what this repo can prove.
    python dev/repo_description_drift.py --truth-module dev/description_truth.py --list-truth

Exit codes::

    0  every numeric claim that could be checked matched (or there were none)
    1  DRIFT — at least one claim contradicts computed truth
    2  could not run the check (bad arguments, provider error, fetch failure)
    3  an unverifiable claim was present and --fail-on-unverified was passed

Deliberately stdlib-only and import-free of the product package, so it runs on
a bare interpreter in CI with no dependency install.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_API = "https://api.github.com/repos/{repo}"
DEFAULT_TIMEOUT = 20.0

# How many words after a number are considered as the candidate unit phrase.
# 3 covers "tools in the core preset"-shaped units without dragging in a whole
# clause; candidates are tried longest-first so "244 MCP tools" prefers the
# key ``mcp tools`` over the shorter ``tools``.
MAX_UNIT_WORDS = 3

# A number followed by 1..MAX_UNIT_WORDS words.
#
#   (?<![\w.,/-])      the number must start a token — rejects the "10" in
#                      "13.10" and the "234" in "1,234" (the thousands form is
#                      matched whole by the first alternative instead)
#   (?![\d.]*\.\d)     rejects version-shaped numbers ("13.10", "0.2.0")
#   word chars         letters/digits/+#&/'- but NOT "." so a trailing
#                      sentence period never becomes part of the unit
_CLAIM_RE = re.compile(
    r"(?<![\w.,/-])"
    r"(\d{1,3}(?:,\d{3})+|\d+)"
    r"(?![\d.]*\.\d)"
    r"((?:\s+[A-Za-z][A-Za-z0-9+#&/'’-]*)" + f"{{1,{MAX_UNIT_WORDS}}})"
)

# A unit is a noun phrase, so it ends at the first function word. Without this
# cut, "5 verbs and no counts" reports the unit as "verbs and no" — technically
# what was captured, but unreadable in a failure message and impossible to
# supply a truth key for.
_UNIT_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "over",
        "per",
        "plus",
        "that",
        "the",
        "to",
        "which",
        "with",
    }
)

_STATUS_VERIFIED = "verified"
_STATUS_MISMATCH = "mismatch"
_STATUS_UNVERIFIED = "unverified"


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """One ``<number> <unit>`` assertion lifted out of the description."""

    value: int
    unit: str  # normalized full candidate phrase (lowercased, collapsed)
    text: str  # the claim as written, e.g. "244 MCP tools"


def _normalize(phrase: str) -> str:
    """Lowercase, drop decoration, collapse whitespace.

    ``"MCP tools"`` and ``"  mcp   Tools "`` both normalize to ``mcp tools``
    so a truth key written either way matches a description written the other.
    """
    lowered = phrase.lower().replace("’", "'")
    cleaned = re.sub(r"[^a-z0-9+#&/'-]+", " ", lowered)
    return " ".join(cleaned.split())


def _trim_unit(unit: str) -> str:
    """Cut a captured phrase at its first function word.

    Falls back to the untrimmed phrase when the cut would leave nothing, so a
    claim is never silently dropped just because it reads awkwardly.
    """
    words = unit.split()
    kept: list[str] = []
    for word in words:
        if word in _UNIT_STOP_WORDS:
            break
        kept.append(word)
    return " ".join(kept) if kept else unit


def extract_claims(description: str) -> list[Claim]:
    """Return every ``<number> <unit phrase>`` claim in ``description``.

    Digit-form numbers only. Word-form numerals ("zero API keys", "five core
    verbs") are deliberately NOT extracted: they are near-universally
    rhetorical rather than derived counts, and lifting them produces noise
    that trains readers to ignore this gate.
    """
    claims: list[Claim] = []
    for match in _CLAIM_RE.finditer(description or ""):
        raw_number, raw_unit = match.group(1), match.group(2)
        value = int(raw_number.replace(",", ""))
        unit = _trim_unit(_normalize(raw_unit))
        if not unit:
            continue
        # Render the claim as written (original casing) but trimmed to the
        # same words as the matched unit, so the report echoes the phrase the
        # reader is being asked to reason about.
        as_written = " ".join(raw_unit.split()[: len(unit.split())])
        claims.append(Claim(value=value, unit=unit, text=f"{raw_number} {as_written}"))
    return claims


def _unit_candidates(unit: str) -> list[str]:
    """Sub-phrases of ``unit``, longest first (``mcp tools`` before ``tools``)."""
    words = unit.split()
    return [" ".join(words[i:]) for i in range(len(words))]


def _singular_plural_variants(key: str) -> set[str]:
    """Naive English number-agreement variants of a truth key.

    A description may say "1 command" where the truth map says "commands".
    Matching is on the *unit*, so tolerating agreement is the difference
    between a working gate and a wall of false UNVERIFIED lines.
    """
    words = key.split()
    if not words:
        return set()
    head, last = words[:-1], words[-1]
    out = {key}
    if last.endswith("ies") and len(last) > 3:
        out.add(" ".join([*head, last[:-3] + "y"]))
    elif last.endswith("ses") and len(last) > 3:
        out.add(" ".join([*head, last[:-2]]))
    elif last.endswith("s") and len(last) > 1:
        out.add(" ".join([*head, last[:-1]]))
    else:
        out.add(" ".join([*head, last + "s"]))
        out.add(" ".join([*head, last + "es"]))
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    claim: Claim
    status: str
    truth_key: str | None = None
    truth_value: int | None = None


def _build_lookup(truth: dict[str, int]) -> dict[str, str]:
    """Map every matchable phrase to its canonical truth key.

    Exact keys always win over generated agreement variants, so a truth map
    carrying both ``command`` and ``commands`` stays unambiguous.
    """
    lookup: dict[str, str] = {}
    for key in truth:
        for variant in _singular_plural_variants(_normalize(key)):
            lookup.setdefault(variant, key)
    for key in truth:  # exact spellings override any variant collision
        lookup[_normalize(key)] = key
    return lookup


def compare(claims: list[Claim], truth: dict[str, int]) -> list[Finding]:
    """Resolve each claim against ``truth``; never raises on unknown units."""
    lookup = _build_lookup(truth)
    findings: list[Finding] = []
    for claim in claims:
        resolved: str | None = None
        for candidate in _unit_candidates(claim.unit):
            if candidate in lookup:
                resolved = lookup[candidate]
                break
        if resolved is None:
            findings.append(Finding(claim=claim, status=_STATUS_UNVERIFIED))
            continue
        expected = int(truth[resolved])
        status = _STATUS_VERIFIED if expected == claim.value else _STATUS_MISMATCH
        findings.append(Finding(claim=claim, status=status, truth_key=resolved, truth_value=expected))
    return findings


# ---------------------------------------------------------------------------
# Inputs: the live description, and the truth map
# ---------------------------------------------------------------------------


class FetchError(RuntimeError):
    """The live description could not be read."""


def fetch_description(repo: str, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Return the live GitHub description for ``owner/name``.

    Works unauthenticated against public repositories; a token (when the
    environment supplies one) only buys a saner rate limit. Returns "" for a
    repository whose description is unset — an empty description makes no
    numeric claim, which the caller treats as a pass.
    """
    if "/" not in repo:
        raise FetchError(f"--repo must be OWNER/NAME, got {repo!r}")
    request = urllib.request.Request(  # noqa: S310 — fixed https api.github.com origin
        GITHUB_API.format(repo=repo),
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-description-drift",
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover — network shape
        raise FetchError(f"GitHub API returned HTTP {exc.code} for {repo}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:  # pragma: no cover
        raise FetchError(f"could not read the GitHub description for {repo}: {exc}") from exc
    return payload.get("description") or ""


def load_truth_module(path: Path) -> dict[str, int]:
    """Load a truth provider: a Python file exposing ``truth() -> dict``.

    The provider — not this script — owns *how* the numbers are derived, which
    is what makes the gate reusable across repositories. It must return a
    mapping of unit phrase to integer.
    """
    resolved = path.resolve()
    if not resolved.is_file():
        raise FetchError(f"--truth-module {path} does not exist")
    name = f"_truth_{resolved.stem}"
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:
        raise FetchError(f"--truth-module {path} is not importable")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so a provider that uses @dataclass (directly or
    # transitively) can resolve ``sys.modules[cls.__module__]``.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    provider = getattr(module, "truth", None)
    if not callable(provider):
        raise FetchError(f"--truth-module {path} does not define a callable truth()")
    return _coerce_truth(provider(), source=str(path))


def _coerce_truth(raw: object, *, source: str) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise FetchError(f"{source} produced {type(raw).__name__}, expected a dict of unit -> int")
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError) as exc:
            raise FetchError(f"{source} has a non-integer truth value for {key!r}: {value!r}") from exc
    return out


def _parse_inline_truth(pairs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for pair in pairs:
        if "=" not in pair:
            raise FetchError(f"--truth expects 'unit=NUMBER', got {pair!r}")
        key, _, value = pair.partition("=")
        try:
            out[key.strip()] = int(value.strip())
        except ValueError as exc:
            raise FetchError(f"--truth value for {key.strip()!r} is not an integer: {value.strip()!r}") from exc
    return out


def _github_token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    return None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


_LABELS = {
    _STATUS_VERIFIED: "VERIFIED  ",
    _STATUS_MISMATCH: "MISMATCH  ",
    _STATUS_UNVERIFIED: "UNVERIFIED",
}


def report(findings: list[Finding], *, repo: str, description: str, truth: dict[str, int]) -> None:
    """Print the human-readable verdict for ``findings`` to stdout."""
    print(f"[repo-description-drift] repo={repo}")
    print(f'  description: "{description}"')
    print(f"  truth: {', '.join(f'{k}={v}' for k, v in sorted(truth.items())) or '(empty)'}")
    if not findings:
        print("  no numeric claims in the description — nothing to verify")
        return
    for finding in findings:
        label = _LABELS[finding.status]
        if finding.status == _STATUS_UNVERIFIED:
            detail = f"no computed truth for unit '{finding.claim.unit}'"
        else:
            detail = f"{finding.truth_key} = {finding.truth_value}"
        print(f"  {label}  {finding.claim.text:<28}  ({detail})")


def _json_report(findings: list[Finding], *, repo: str, description: str, truth: dict[str, int]) -> str:
    return json.dumps(
        {
            "repo": repo,
            "description": description,
            "truth": truth,
            "claims": [
                {
                    "text": f.claim.text,
                    "value": f.claim.value,
                    "unit": f.claim.unit,
                    "status": f.status,
                    "truth_key": f.truth_key,
                    "truth_value": f.truth_value,
                }
                for f in findings
            ],
        },
        indent=2,
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(
    *,
    repo: str,
    description: str,
    truth: dict[str, int],
    fail_on_unverified: bool = False,
    as_json: bool = False,
) -> int:
    findings = compare(extract_claims(description), truth)
    if as_json:
        print(_json_report(findings, repo=repo, description=description, truth=truth))
    else:
        report(findings, repo=repo, description=description, truth=truth)

    mismatches = [f for f in findings if f.status == _STATUS_MISMATCH]
    unverified = [f for f in findings if f.status == _STATUS_UNVERIFIED]
    if mismatches:
        print("", file=sys.stderr)
        print(f"DRIFT: the GitHub description of {repo} contradicts computed truth:", file=sys.stderr)
        for finding in mismatches:
            print(
                f"  says {finding.claim.text!r}; truth is {finding.truth_value} {finding.truth_key}",
                file=sys.stderr,
            )
        print(
            "Fix: edit the repository description on GitHub "
            f"(https://github.com/{repo} -> About -> the gear icon), or "
            "`gh repo edit " + repo + ' --description "..."`.',
            file=sys.stderr,
        )
        return 1
    if unverified and fail_on_unverified:
        print("", file=sys.stderr)
        print(
            "UNVERIFIED: --fail-on-unverified was set and the description makes "
            "numeric claims with no computed truth: " + ", ".join(f"{f.claim.text!r}" for f in unverified),
            file=sys.stderr,
        )
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the numbers in a GitHub repository description against computed truth."
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="OWNER/NAME of the repository to check (default: $GITHUB_REPOSITORY).",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Check this literal string instead of fetching from GitHub (offline tests / demos).",
    )
    parser.add_argument(
        "--truth-module",
        type=Path,
        default=None,
        help="Path to a Python file exposing truth() -> dict[str, int] of computed counts.",
    )
    parser.add_argument(
        "--truth-json",
        type=str,
        default=None,
        help="Path to a JSON object of unit -> count ('-' reads stdin). Merged over --truth-module.",
    )
    parser.add_argument(
        "--truth",
        action="append",
        default=[],
        metavar="UNIT=N",
        help="Inline truth pair; repeatable. Highest precedence.",
    )
    parser.add_argument(
        "--fail-on-unverified",
        action="store_true",
        help="Also exit non-zero when the description makes a claim with no computed truth.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit a JSON report.")
    parser.add_argument("--list-truth", action="store_true", help="Print the resolved truth map and exit.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="GitHub API timeout in seconds.")
    args = parser.parse_args(argv)

    try:
        truth: dict[str, int] = {}
        if args.truth_module is not None:
            truth.update(load_truth_module(args.truth_module))
        if args.truth_json is not None:
            raw = sys.stdin.read() if args.truth_json == "-" else Path(args.truth_json).read_text(encoding="utf-8")
            truth.update(_coerce_truth(json.loads(raw), source=args.truth_json))
        truth.update(_parse_inline_truth(args.truth))

        if args.list_truth:
            print(json.dumps(truth, indent=2, sort_keys=True))
            return 0

        if not truth:
            raise FetchError("no truth supplied — pass --truth-module, --truth-json, and/or --truth")

        if args.description is not None:
            description = args.description
            repo = args.repo or "(local --description)"
        else:
            if not args.repo:
                raise FetchError("--repo is required (or set $GITHUB_REPOSITORY) when not using --description")
            repo = args.repo
            description = fetch_description(repo, token=_github_token(), timeout=args.timeout)
    except FetchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    return run(
        repo=repo,
        description=description,
        truth=truth,
        fail_on_unverified=args.fail_on_unverified,
        as_json=args.as_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
