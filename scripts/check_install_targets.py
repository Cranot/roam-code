#!/usr/bin/env python3
"""W1502 — every documented install target must EXIST.

The gate this repository was missing. ``scripts/sync_surface_counts.py``
guarantees that install pins all name the SAME version; nothing asserted that
the version they name can actually be fetched. Those are different questions,
and for 33 commits public ``main`` answered the first one green while the
second was false: every pip pin in the tree 404'd from PyPI and every action
ref resolved to nothing, because the declared version had never been released.
The one check that could have noticed
(``verify_release.py --pypi``) ran only from the publish workflow — i.e. only
after the thing it was meant to prevent had already shipped.

This gate runs on the push path instead, and it is offline by default: a
``v<version>`` tag in this repository is what ``Cranot/roam-code@v<version>``
resolves against, so tag existence is checkable with no network at all, in
milliseconds. ``--pypi`` additionally confirms the wheel is on PyPI, which the
tag cannot prove (a tag can exist while the publish job failed).

FAIL-CLOSED, which is the entire point of the design:

    exit 0  OK       every install target named in the tree exists
    exit 1  FAIL     a target does not exist -- naming it is an instruction
                     that cannot be followed
    exit 2  UNKNOWN  the gate could not determine whether a target exists
                     (no git, no tags, PyPI unreachable, an HTTP error that
                     is not a clean 404). It REFUSES. It does not pass.

The UNKNOWN path is not a formality. "The registry was unreachable, so assume
the version is fine" is precisely the defect class this repository keeps
closing elsewhere, and shipping it inside the guard against that class would
be the worst possible place to put it. An absent measurement is UNKNOWN, never
a benign default.

Usage::

    python scripts/check_install_targets.py           # offline: tags only
    python scripts/check_install_targets.py --pypi    # also confirm on PyPI
    python scripts/check_install_targets.py --json    # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import sync_surface_counts as sync  # noqa: E402

OK = 0
FAIL = 1
UNKNOWN = 2

PYPI_URL = "https://pypi.org/pypi/roam-code/{version}/json"


class Unknown(Exception):
    """The gate could not determine an answer, so it must refuse.

    Distinct from a failure on purpose. Collapsing "I could not check" into
    either "pass" or "fail" loses the only information the operator needs to
    decide what to do next.
    """


def _tag_set() -> set[str]:
    """Release tags in this repository, or ``Unknown`` if they cannot be read."""
    try:
        return set(sync.release_tags())
    except SystemExit as exc:  # release_tags hard-fails when git is unusable
        raise Unknown(str(exc)) from exc


def _pypi_has(version: str) -> bool:
    """True/False if PyPI answered; ``Unknown`` if it could not be asked.

    Only a clean 404 counts as "not published". Every other outcome -- a 5xx,
    a timeout, a proxy interception, a DNS failure -- means the question was
    not answered, and an unanswered question is UNKNOWN.
    """
    url = PYPI_URL.format(version=version)
    req = urllib.request.Request(url, headers={"User-Agent": "roam-code-install-target-gate"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise Unknown(f"PyPI returned HTTP {exc.code} for {version}; existence not determined") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Unknown(f"PyPI unreachable while checking {version}: {exc}") from exc


def check(*, check_pypi: bool) -> tuple[int, dict]:
    """Return ``(exit_code, report)``."""
    report: dict = {"checked_pypi": check_pypi}

    sites = sync.install_pin_sites()
    report["install_pin_sites"] = len(sites)
    versions = sorted({v for _, _, v in sites})
    report["versions"] = versions

    if not sites:
        # A sweep that found nothing is far more likely to be a broken sweep
        # than a tree with no install instructions -- this repository ships
        # seven CI templates that each carry one. Treating an empty result as
        # "all clear" is how a gate becomes a no-op that still prints OK.
        report["status"] = "UNKNOWN"
        report["reason"] = (
            "no install pins found in the tracked tree; the sweep is expected to "
            "match the shipped CI templates, so an empty result reads as a broken "
            "scanner rather than a clean tree"
        )
        return UNKNOWN, report

    try:
        tags = _tag_set()
    except Unknown as exc:
        report["status"] = "UNKNOWN"
        report["reason"] = str(exc)
        return UNKNOWN, report

    missing_tag = [v for v in versions if f"v{v}" not in tags]
    report["missing_tag"] = missing_tag

    missing_pypi: list[str] = []
    if check_pypi:
        for v in versions:
            try:
                if not _pypi_has(v):
                    missing_pypi.append(v)
            except Unknown as exc:
                report["status"] = "UNKNOWN"
                report["reason"] = str(exc)
                return UNKNOWN, report
    report["missing_pypi"] = missing_pypi

    if missing_tag or missing_pypi:
        report["status"] = "FAIL"
        report["offending_sites"] = [
            {"path": rel, "line": line, "version": v}
            for rel, line, v in sites
            if v in set(missing_tag) | set(missing_pypi)
        ]
        return FAIL, report

    report["status"] = "OK"
    return OK, report


def _print_human(code: int, report: dict) -> None:
    versions = ", ".join(report.get("versions", [])) or "(none)"
    if code == UNKNOWN:
        print("UNKNOWN: cannot determine whether the documented install targets exist.", file=sys.stderr)
        print(f"  reason: {report.get('reason')}", file=sys.stderr)
        print(
            "  Refusing rather than passing. An unreachable registry is not evidence\n"
            "  that a version exists; if it were, this gate would be the defect it guards.",
            file=sys.stderr,
        )
        return
    if code == FAIL:
        print(
            f"FAIL: this repository tells readers and machines to install {versions}, which does not exist.",
            file=sys.stderr,
        )
        for v in report.get("missing_tag", []):
            print(f"  no tag v{v} in this repository -> `Cranot/roam-code@v{v}` cannot resolve", file=sys.stderr)
        for v in report.get("missing_pypi", []):
            print(f"  roam-code=={v} is not on PyPI -> `pip install` fails at the consumer", file=sys.stderr)
        print("  sites:", file=sys.stderr)
        for site in report.get("offending_sites", []):
            print(f"    {site['path']}:{site['line']}: {site['version']}", file=sys.stderr)
        print(
            "\n  Fix the INSTRUCTION, not the gate: run\n"
            "    python scripts/sync_surface_counts.py --write\n"
            "  which pins install-shaped sites to the last published release. If you\n"
            "  intended to release, publish and tag first -- the pins follow the tag.",
            file=sys.stderr,
        )
        return
    scope = "tag + PyPI" if report["checked_pypi"] else "tag (offline; use --pypi to also confirm the wheel)"
    print(f"OK: {report['install_pin_sites']} install pin(s) name {versions}, which exists [{scope}].")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--pypi",
        action="store_true",
        help="also confirm each pinned version is on PyPI (needs network; UNKNOWN if unreachable)",
    )
    ap.add_argument("--json", action="store_true", help="emit the report as JSON on stdout")
    args = ap.parse_args(argv)

    code, report = check(check_pypi=args.pypi)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(code, report)
    return code


if __name__ == "__main__":
    sys.exit(main())
