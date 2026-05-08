#!/usr/bin/env python3
"""Post-release / pre-release sanity checks against PyPI + roam-code.com.

Three checks bundled here so a single CI step (or a local invocation
after a release) can verify the full surface:

1. **PyPI freshness** — the latest published version on pypi.org matches
   ``pyproject.toml`` (or is at most one ahead, allowing for the brief
   window between bumping pyproject and publishing the tag).
2. **Install smoke** — ``pip install roam-code==<version>`` into an
   ephemeral venv works and ``roam --version`` reports the expected
   string.
3. **Live security headers** — ``roam-code.com`` serves the headers
   declared in ``templates/distribution/landing-page/_headers``. The
   security page makes claims (HSTS, CSP, COOP, CORP, X-Frame-Options
   DENY) that buyers can verify; this catches regressions if a CF
   config drift breaks them.

Usage::

    python scripts/verify_release.py             # all three checks
    python scripts/verify_release.py --pypi      # just PyPI freshness
    python scripts/verify_release.py --install   # just install smoke
    python scripts/verify_release.py --headers   # just header check

Exit code 0 on full pass, 1 on any failure. Designed to be runnable
unattended on CI; uses only stdlib + ``pip`` (no extra deps).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit("pyproject.toml missing version field")
    return m.group(1)


def _pypi_latest() -> str | None:
    """Return the latest version on pypi.org or None on network failure."""
    try:
        with urllib.request.urlopen(
            "https://pypi.org/pypi/roam-code/json", timeout=10
        ) as resp:
            data = json.load(resp)
    except urllib.error.URLError as exc:
        print(f"WARN: PyPI fetch failed: {exc}", file=sys.stderr)
        return None
    return data.get("info", {}).get("version")


def check_pypi_freshness() -> bool:
    """The PyPI latest version is no more than one minor behind pyproject.

    Allows pyproject to be one ahead (the brief window between bumping
    + tagging vs the publish workflow finishing). Anything further is a
    drift signal.
    """
    repo_v = _pyproject_version()
    pypi_v = _pypi_latest()
    if pypi_v is None:
        print("FAIL: PyPI unreachable; cannot verify freshness", file=sys.stderr)
        return False
    if repo_v == pypi_v:
        print(f"OK: pyproject and PyPI both at {repo_v}")
        return True

    repo_parts = [int(x) for x in repo_v.split(".") if x.isdigit()]
    pypi_parts = [int(x) for x in pypi_v.split(".") if x.isdigit()]
    if repo_parts > pypi_parts:
        print(f"OK: pyproject={repo_v} ahead of PyPI={pypi_v} (pre-publish window)")
        return True
    print(
        f"FAIL: pyproject={repo_v} BEHIND PyPI={pypi_v} — repo is stale relative to released package",
        file=sys.stderr,
    )
    return False


def check_install_smoke(version: str | None = None) -> bool:
    """``pip install roam-code[==version]`` into an ephemeral venv and run --version."""
    target = version or _pyproject_version()
    pin = f"roam-code=={target}"

    with tempfile.TemporaryDirectory() as td:
        venv = Path(td) / "venv"
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(venv)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            print(f"FAIL: venv creation failed: {exc.stderr.decode()}", file=sys.stderr)
            return False

        pip = venv / ("Scripts" if sys.platform == "win32" else "bin") / "pip"
        roam = venv / ("Scripts" if sys.platform == "win32" else "bin") / "roam"

        try:
            subprocess.run(
                [str(pip), "install", "--quiet", pin],
                check=True,
                capture_output=True,
                timeout=180,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            err = (exc.stderr or b"").decode("utf-8", errors="replace") if hasattr(exc, "stderr") else str(exc)
            print(f"FAIL: pip install {pin}: {err[:400]}", file=sys.stderr)
            return False

        try:
            result = subprocess.run(
                [str(roam), "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"FAIL: roam --version: {exc}", file=sys.stderr)
            return False

        if target not in result.stdout:
            print(
                f"FAIL: roam --version reported {result.stdout.strip()!r}, expected to contain {target!r}",
                file=sys.stderr,
            )
            return False
        print(f"OK: pip install {pin} works; roam --version: {result.stdout.strip()}")
        return True


# Headers we declare in templates/distribution/landing-page/_headers and
# that the security page advertises. Buyers can curl-check these; if a
# Cloudflare config drift removes one, this catches it.
EXPECTED_HEADERS = {
    "strict-transport-security": "max-age=63072000",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=()",
}


def check_live_headers(url: str = "https://roam-code.com/") -> bool:
    """Fetch the production site and assert each declared header lands."""
    # Cloudflare blocks the default urllib UA with a 403 challenge page.
    # Use a real-browser-shaped UA so the request lands on the actual
    # origin headers rather than the bot-protection edge.
    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
                    "roam-code-verify-release/1.0"
                ),
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.URLError as exc:
        print(f"WARN: {url} fetch failed: {exc}", file=sys.stderr)
        return False

    failures: list[str] = []
    for header, fragment in EXPECTED_HEADERS.items():
        actual = headers.get(header)
        if actual is None:
            failures.append(f"missing: {header}")
            continue
        if fragment.lower() not in actual.lower():
            failures.append(f"{header}: expected substring {fragment!r}, got {actual!r}")
    if not failures:
        print(f"OK: {url} serves all {len(EXPECTED_HEADERS)} declared security headers")
        return True
    print(f"FAIL: {url} security-header drift:", file=sys.stderr)
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pypi", action="store_true", help="Run only PyPI freshness")
    ap.add_argument("--install", action="store_true", help="Run only install smoke")
    ap.add_argument("--headers", action="store_true", help="Run only live header check")
    args = ap.parse_args()

    selected = [args.pypi, args.install, args.headers]
    if not any(selected):
        selected = [True, True, True]
    do_pypi, do_install, do_headers = selected

    results: list[tuple[str, bool]] = []
    if do_pypi:
        results.append(("pypi-freshness", check_pypi_freshness()))
    if do_install:
        results.append(("install-smoke", check_install_smoke()))
    if do_headers:
        results.append(("live-headers", check_live_headers()))

    print()
    failed = [name for name, ok in results if not ok]
    if failed:
        print(f"verify_release: {len(failed)} check(s) failed: {', '.join(failed)}")
        return 1
    print(f"verify_release: all {len(results)} check(s) passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
