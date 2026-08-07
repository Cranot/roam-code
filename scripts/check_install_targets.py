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
``v<version>`` tag is what ``Cranot/roam-code@v<version>`` resolves against,
so tag existence is checkable with no network at all, in milliseconds.

**What the offline check does and does not prove.** It reads the LOCAL tag
list. A tag that exists locally and has never been pushed satisfies it while
every consumer's ``Cranot/roam-code@v<version>`` still resolves to nothing —
which is a live hazard on the release path, where the tag is created locally
first. ``--remote`` closes that half by asking GitHub for the ref; ``--pypi``
closes the wheel half, which no tag can prove (a tag can exist while the
publish job failed). ``--network`` is both. The default is stated this way
rather than implied because "the local tag list agrees" is a weaker claim than
"a consumer can fetch this", and only the second one is what an install
instruction promises.

FAIL-CLOSED, which is the entire point of the design:

    exit 0  OK       every install target named in the tree exists
    exit 1  FAIL     a target does not exist -- naming it is an instruction
                     that cannot be followed
    exit 2  UNKNOWN  the gate could not determine whether a target exists.
                     It REFUSES. It does not pass.

Every not-knowing path returns 2, and the list is longer than it first looks:
git unusable; an EMPTY tag list (a shallow clone has none, and "no tags" is
not "no releases"); a tracked file that would not open; a sweep that matched
nothing; a shipped CI template carrying an UNPINNED install, whose target is
therefore whatever is latest at run time; a registry that did not answer; a
registry answer that is not the registry's own JSON for the version asked
about. That last one matters more than its size suggests — an intercepting
proxy answers 200, so "the HTTP status was 2xx" is not evidence that PyPI
said anything.

"The registry was unreachable, so assume the version is fine" is precisely the
defect class this repository keeps closing elsewhere, and shipping it inside
the guard against that class would be the worst possible place to put it. An
absent measurement is UNKNOWN, never a benign default.

Usage::

    python scripts/check_install_targets.py            # offline: local tags
    python scripts/check_install_targets.py --pypi     # + the wheel on PyPI
    python scripts/check_install_targets.py --remote   # + the tag on GitHub
    python scripts/check_install_targets.py --network  # both of the above
    python scripts/check_install_targets.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import sync_surface_counts as sync  # noqa: E402

OK = 0
FAIL = 1
UNKNOWN = 2

PYPI_URL = "https://pypi.org/pypi/roam-code/{version}/json"
GITHUB_REF_URL = "https://api.github.com/repos/Cranot/roam-code/git/ref/tags/v{version}"

_USER_AGENT = "roam-code-install-target-gate"
_MAX_BODY = 4 * 1024 * 1024


class Unknown(Exception):
    """The gate could not determine an answer, so it must refuse.

    Distinct from a failure on purpose. Collapsing "I could not check" into
    either "pass" or "fail" loses the only information the operator needs to
    decide what to do next.
    """


class _Absent(Exception):
    """A clean 404 — the ONLY outcome that is evidence a thing is not there."""


def _tag_set() -> set[str]:
    """Release tags in this repository, or ``Unknown`` if they cannot be read.

    An EMPTY list is a not-knowing path, not a clean one. ``git tag --list``
    exits 0 with no output in a shallow clone — the single most common way a
    stranger gets this repository, and the default of every CI checkout that
    does not set ``fetch-depth: 0``. Letting the empty set flow into the
    membership test below marks EVERY pinned version missing and reports FAIL:
    a false assertion of absence manufactured from missing data, which is the
    same defect as the false assertion of presence, pointed the other way.
    """
    try:
        tags = set(sync.release_tags())
    except SystemExit as exc:  # release_tags hard-fails when git is unusable
        raise Unknown(str(exc)) from exc
    if not tags:
        raise Unknown(
            "this repository has no `v*` release tags, so no install target can be "
            "resolved against it. That is almost always a shallow or tagless clone "
            "rather than a project with no releases -- run `git fetch --tags` (or "
            "check out with `fetch-depth: 0`) and re-run. Refusing rather than "
            "reporting every pin missing, which would be an assertion of absence "
            "built out of missing data"
        )
    return tags


def _fetch_json(url: str, *, what: str) -> dict:
    """Parsed JSON from ``url``; ``_Absent`` on a clean 404; else ``Unknown``.

    Deliberately NOT "the status was 2xx". A captive portal, a corporate MITM
    proxy with a trusted root, or any other intermediary answers 200 with its
    own page, and ``urllib``'s default opener honours ``https_proxy`` from the
    environment — so 2xx-means-yes turns the exact situation this gate exists
    for ("the registry could not be reached") into "the version exists". Four
    things must all hold before an answer counts as the registry's answer:
    the status is exactly 200, the final URL is still the host we asked, the
    content type is JSON, and the body parses. Anything else is UNKNOWN.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            final_url = resp.geturl()
            content_type = resp.headers.get_content_type()
            body = resp.read(_MAX_BODY)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _Absent(f"{what}: 404") from exc
        raise Unknown(f"{what}: HTTP {exc.code}; existence not determined") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Unknown(f"{what}: unreachable ({exc}); existence not determined") from exc

    if status != 200:
        raise Unknown(f"{what}: HTTP {status} is not an answer this gate reads as data")
    asked_host = urllib.parse.urlsplit(url).netloc
    answered_host = urllib.parse.urlsplit(final_url).netloc
    if answered_host != asked_host:
        raise Unknown(
            f"{what}: asked {asked_host} and was answered by {answered_host}; "
            "an intermediary answered, so the registry did not"
        )
    if content_type != "application/json":
        raise Unknown(
            f"{what}: answered with content-type {content_type!r}, not application/json; "
            "that is an intermediary's page, not the registry's record"
        )
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Unknown(f"{what}: answered 200 with a body that is not JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise Unknown(f"{what}: answered 200 with JSON that is a {type(data).__name__}, not an object")
    return data


def _pypi_has(version: str) -> bool:
    """True/False if PyPI answered about THIS version; ``Unknown`` otherwise.

    The payload is checked against the version asked for. A 200 carrying some
    other release is an answer to a different question, and reading it as yes
    is how a cached or rewritten response certifies a version that was never
    published.
    """
    try:
        data = _fetch_json(PYPI_URL.format(version=version), what=f"PyPI roam-code {version}")
    except _Absent:
        return False
    reported = (data.get("info") or {}).get("version")
    if reported != version:
        raise Unknown(
            f"PyPI roam-code {version}: the 200 response describes {reported!r} instead; "
            "that is an answer about a different version, not about this one"
        )
    return True


def _remote_tag_has(version: str) -> bool:
    """Whether ``v<version>`` exists on the GitHub remote; ``Unknown`` if unasked.

    The local tag list cannot see this. ``git tag v14.0.0`` followed by a sync
    and this gate passes offline while every consumer's action ref still
    resolves to nothing, because the tag was never pushed — and that sequence
    is exactly the release path.
    """
    try:
        data = _fetch_json(GITHUB_REF_URL.format(version=version), what=f"GitHub tag v{version}")
    except _Absent:
        return False
    ref = data.get("ref")
    if ref != f"refs/tags/v{version}":
        raise Unknown(
            f"GitHub tag v{version}: the 200 response describes ref {ref!r}; "
            "that is not the ref this pin resolves against"
        )
    return True


def _unpinned_shipped_surfaces(sites: list[tuple[str, int, str]]) -> list[str]:
    """Shipped CI templates that carry NO install pin the sweep can see.

    A pin scanner can only check pins that exist. A template whose install
    line reads ``pip install roam-code`` produces no site at all, so it is
    absent from the report, absent from the count, and reported OK by a gate
    that never evaluated it. Its install target is "whatever is latest when
    this runs", which is not a target this gate — or anyone — can check.
    """
    with_pins = {rel for rel, _, _ in sites}
    return [rel for rel in sync.shipped_install_surfaces() if rel not in with_pins]


def check(*, check_pypi: bool, check_remote: bool) -> tuple[int, dict]:
    """Return ``(exit_code, report)``.

    Both network switches are required keywords. A defaulted one reads as
    "off" at a call site that forgot it, which silently downgrades the claim
    from "a consumer can fetch this" to "the local tag list agrees" — the
    weakening this module exists to make impossible.
    """
    report: dict = {"checked_pypi": check_pypi, "checked_remote": check_remote}

    sites, scan = sync.install_pin_scan()
    report["install_pin_sites"] = len(sites)
    report["scan"] = scan
    versions = sorted({v for _, _, v in sites})
    report["versions"] = versions

    def _refuse(reason: str) -> tuple[int, dict]:
        report["status"] = "UNKNOWN"
        report["reason"] = reason
        return UNKNOWN, report

    if scan["unreadable"]:
        return _refuse(
            f"{scan['unreadable']} tracked file(s) could not be read, so any install "
            "instruction they carry was never seen; an unread file is not an empty one"
        )

    if not sites:
        # A sweep that found nothing is far more likely to be a broken sweep
        # than a tree with no install instructions -- this repository ships
        # CI templates that each carry one. Treating an empty result as
        # "all clear" is how a gate becomes a no-op that still prints OK.
        return _refuse(
            "no install pins found in the tracked tree; the sweep is expected to "
            "match the shipped CI templates, so an empty result reads as a broken "
            "scanner rather than a clean tree"
        )

    unpinned = _unpinned_shipped_surfaces(sites)
    report["unpinned_shipped_surfaces"] = unpinned
    if unpinned:
        return _refuse(
            "shipped CI template(s) install roam-code without pinning a version, so "
            "the target they name is whatever is latest at run time and cannot be "
            "checked: " + ", ".join(unpinned)
        )

    try:
        tags = _tag_set()
    except Unknown as exc:
        return _refuse(str(exc))

    missing_tag = [v for v in versions if f"v{v}" not in tags]
    report["missing_tag"] = missing_tag

    missing_pypi: list[str] = []
    missing_remote: list[str] = []
    for v in versions:
        try:
            if check_pypi and not _pypi_has(v):
                missing_pypi.append(v)
            if check_remote and not _remote_tag_has(v):
                missing_remote.append(v)
        except Unknown as exc:
            return _refuse(str(exc))
    report["missing_pypi"] = missing_pypi
    report["missing_remote"] = missing_remote

    if missing_tag or missing_pypi or missing_remote:
        report["status"] = "FAIL"
        gone = set(missing_tag) | set(missing_pypi) | set(missing_remote)
        report["offending_sites"] = [{"path": rel, "line": line, "version": v} for rel, line, v in sites if v in gone]
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
        for v in report.get("missing_remote", []):
            print(
                f"  no tag v{v} on the GitHub remote -> consumers cannot resolve it even if it exists locally",
                file=sys.stderr,
            )
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
    scopes = ["local tag"]
    if report["checked_remote"]:
        scopes.append("GitHub remote tag")
    if report["checked_pypi"]:
        scopes.append("PyPI")
    scope = " + ".join(scopes)
    if not (report["checked_pypi"] and report["checked_remote"]):
        scope += "; use --network for the rest"
    scan = report.get("scan", {})
    print(f"OK: {report['install_pin_sites']} install pin(s) name {versions}, which exists [{scope}].")
    if scan:
        # The denominator. "44 pins are fine" says nothing about the files the
        # sweep declined to read, and every number below was a silent skip.
        print(
            f"  scanned {scan['scanned']} of {scan['tracked']} tracked files "
            f"(skipped: {scan['no_pin_token']} with no `roam` token, {scan['exempt']} exempt, "
            f"{scan['unreadable']} unreadable; {scan['non_utf8']} decoded with replacement)"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--pypi",
        action="store_true",
        help="also confirm each pinned version is on PyPI (needs network; UNKNOWN if unreachable)",
    )
    ap.add_argument(
        "--remote",
        action="store_true",
        help="also confirm each pinned tag exists on the GitHub REMOTE, which the local tag list cannot show",
    )
    ap.add_argument("--network", action="store_true", help="shorthand for --pypi --remote")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON on stdout")
    args = ap.parse_args(argv)

    code, report = check(
        check_pypi=args.pypi or args.network,
        check_remote=args.remote or args.network,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(code, report)
    return code


if __name__ == "__main__":
    sys.exit(main())
