"""Accept deployed site content against a clean, source-checked Git commit.

No publication or dashboard mutation. Fetch every committed public file, not
just the changelog. Retain raw responses; allow only the known Cloudflare email
transform. Historical content is compared with its historical source, not
rewritten to today's counts. Run --source-only in CI; run the network check
after an authorized deployment. Requires system curl with normal TLS validation.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_site_product_facts as facts_builder  # noqa: E402
from scripts.stage_site import SITE, git, require_candidate  # noqa: E402

REQUIRED = {
    "index.html",
    "setup.html",
    "pricing.html",
    "audit.html",
    "compare.html",
    "press.html",
    "status.html",
    "measurements.html",
    "llms.txt",
    "docs/getting-started.html",
    "docs/agent-contract.html",
    "docs/canonical-demo.html",
    "docs/architecture.html",
    "docs/command-reference.html",
    "docs/mcp-usage.html",
    "data/product-facts.json",
    "examples/team-replay-report.md",
    "_headers",
    "_redirects",
}
EMAIL_SCRIPT = (
    b'<script data-cfasync="false" src="/cdn-cgi/scripts/5c5dd728/cloudflare-static/email-decode.min.js"></script>'
)
HEADER_NAMES = {
    "Cache-Control",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Strict-Transport-Security",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
    "Reporting-Endpoints",
}
MIME = {
    ".html": {"text/html"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".json": {"application/json"},
    ".css": {"text/css"},
    ".mjs": {"text/javascript", "application/javascript"},
    ".svg": {"image/svg+xml"},
    ".png": {"image/png"},
    ".woff2": {"font/woff2"},
    ".xml": {"application/xml", "text/xml"},
    ".webmanifest": {"application/manifest+json"},
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_check(root: Path) -> dict:
    """Check canonical producers, not a second set of numeric constants."""
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        status = facts_builder.run(root)
    if status:
        raise ValueError("Canonical product facts are stale: " + log.getvalue())
    facts = facts_builder.product_facts(root)
    findings = []
    for path in sorted((root / SITE).rglob("*")):
        if path.suffix in {".html", ".txt", ".md"}:
            findings.extend(
                current_fact_issues(path.relative_to(root / SITE).as_posix(), path.read_text(encoding="utf-8"), facts)
            )
    if findings:
        raise ValueError("Current product fact conflicts: " + json.dumps(findings))
    for script in ("scripts/sync_surface_counts.py", "dev/build_readme_counts.py"):
        argv = [sys.executable, str(root / script)]
        if script.startswith("dev/"):
            argv.append("--check")
        result = subprocess.run(argv, cwd=root, capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError(f"Source consistency failed: {script}: " + result.stdout.decode(errors="replace"))
    return facts


def current_fact_issues(name: str, text: str, facts: dict) -> list[dict]:
    """Bounded known-shape guard; semantic review is still required.

    The generated release archive owns historical counts. Dated measurements
    and installed-package pins are not matched by the current-version pattern.
    Do not add arbitrary whole-page exemptions to hide a current claim.
    """
    if name == "changelog.html":
        return []
    plain = html.unescape(re.sub(r"<[^>]*>", " ", text))
    patterns = {
        "mcpToolCount": r"\b(\d+)\s+MCP\s+tools\b",
        "cliCommandCount": r"\b(\d+)\s+CLI\s+commands\b",
        "languageCount": r"\b(\d+)\s+languages\b",
        "defaultMcpToolCount": r"\b(\d+)\s+(?:default(?:\s+core)?|core)\s+tools\b",
        "sourceVersion": r"\b(?:current(?:\s+(?:release|version))?|source version)\s*[:=]\s*v?(\d+\.\d+\.\d+)",
    }
    issues = []
    for key, pattern in patterns.items():
        for match in re.finditer(pattern, plain, re.I):
            if match[1] != str(facts[key]):
                issues.append({"file": name, "fact": key, "actual": match[1], "expected": facts[key]})
    return issues


def committed_site(root: Path, commit: str) -> dict[str, bytes]:
    require_candidate(root, commit)
    objects = {}
    for entry in git(root, "ls-tree", "-r", "-z", commit, "--", SITE).split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = name.decode("utf-8")
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("Nonregular public file: " + path)
        objects[path] = oid
    algorithm = git(root, "rev-parse", "--show-object-format").decode().strip()
    if algorithm not in {"sha1", "sha256"}:
        raise ValueError("Unsupported Git object format")
    content = {}
    with zipfile.ZipFile(io.BytesIO(git(root, "archive", "--format=zip", commit, "--", SITE))) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            data = archive.read(member)
            oid = hashlib.new(algorithm, f"blob {len(data)}\0".encode() + data).hexdigest()
            if objects.get(member.filename) != oid or member.filename in content:
                raise ValueError("Archive omitted, duplicated or transformed a Git blob")
            content[member.filename] = data
    if set(content) != set(objects):
        raise ValueError("Archive inventory does not match Git")
    site = {name.removeprefix(SITE + "/"): data for name, data in content.items()}
    if REQUIRED - site.keys():
        raise ValueError("Missing required site files: " + str(sorted(REQUIRED - site.keys())))
    for name in site:
        if (
            "\\" in name
            or ":" in name
            or any(
                part in {".", "..", "internal", ".git", ".roam"} or part.startswith(".env") for part in name.split("/")
            )
        ):
            raise ValueError("Unsafe public path: " + name)
    require_candidate(root, commit)
    return site


def route(name: str) -> str:
    if name == "index.html":
        return "/"
    if name.endswith("/index.html"):
        return "/" + name.removesuffix("index.html")
    return "/" + name.removesuffix(".html") if name.endswith(".html") else "/" + name


def validate_base(value: str) -> str:
    if not re.fullmatch(r"https://(?:roam-code\.com|[a-f0-9]{8,64}\.roam-code\.pages\.dev)/?", value):
        raise ValueError("Use the public Roam domain or an exact Pages deployment HTTPS URL")
    return value.rstrip("/")


def email_equivalent(actual: bytes, expected: bytes) -> bool:
    """Restore only observed email shapes, then demand byte identity."""

    def decode(value):
        raw = bytes.fromhex(value.decode("ascii"))
        if len(raw) < 2:
            raise ValueError("Missing email")
        decoded = bytes(v ^ raw[0] for v in raw[1:])
        if b"@" not in decoded or any(v < 32 or v in (34, 60, 62) for v in decoded):
            raise ValueError("Unrecognized email")
        return decoded

    try:
        changes = 0
        for tag in ("a", "span"):
            prefix = b' href="/cdn-cgi/l/email-protection"' if tag == "a" else b""
            pattern = (
                b"<"
                + tag.encode()
                + prefix
                + rb' class="__cf_email__" data-cfemail="([0-9a-f]+)">'
                + rb"\[email&#160;protected\]</"
                + tag.encode()
                + b">"
            )
            actual, count = re.subn(pattern, lambda m: decode(m[1]), actual)
            changes += count
        actual, count = re.subn(
            rb'href="/cdn-cgi/l/email-protection#([0-9a-f]+)"', lambda m: b'href="mailto:' + decode(m[1]) + b'"', actual
        )
        changes += count
        if not changes or actual.count(EMAIL_SCRIPT) != 1:
            return False
        actual = actual.replace(EMAIL_SCRIPT, b"", 1)
        for tag in re.findall(rb'<a\b[^>]*href="mailto:[^"]*"[^>]*>', expected):
            collapsed = re.sub(rb"\n[ \t]+", b" ", tag)
            if collapsed != tag and collapsed in actual:
                if actual.count(collapsed) != expected.count(tag):
                    return False
                actual = actual.replace(collapsed, tag)
        return actual == expected
    except (ValueError, UnicodeError):
        return False


def parse_headers(raw: bytes, status: int) -> dict[str, list[str]]:
    blocks = [part for part in re.split(rb"\r?\n\r?\n", raw) if part.startswith(b"HTTP/")]
    if not blocks:
        raise ValueError("Missing HTTP headers")
    first, separator, fields = blocks[-1].partition(b"\n")
    match = re.fullmatch(rb"HTTP/[0-9.]+ +([0-9]{3})(?: +[^\r\n]*)?\r?", first)
    if not separator or not match or int(match[1]) != status:
        raise ValueError("HTTP status/header mismatch")
    headers = BytesParser().parsebytes(fields)
    return {key.lower(): headers.get_all(key) for key in headers.keys()}


def fetch(url: str, output: Path) -> tuple[dict, bytes]:
    """No redirects, cookies, auth or TLS bypass. Keep actual headers and body."""
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise OSError("System curl unavailable")
    key = sha(url.encode())
    header_path, body_path = output / (key + ".headers"), output / (key + ".body")
    argv = [
        curl,
        "--silent",
        "--show-error",
        "--proto",
        "=https",
        "--connect-timeout",
        "5",
        "--max-time",
        "25",
        "--max-filesize",
        "5000000",
        "--user-agent",
        "Roam-site-acceptance/1",
        "--header",
        "Accept-Encoding: identity",
        "--header",
        "Cache-Control: no-cache",
        "--dump-header",
        str(header_path),
        "--output",
        str(body_path),
        "--write-out",
        "%{http_code}\\n%{url_effective}\\n",
        url,
    ]
    result = subprocess.run(argv, capture_output=True, timeout=35)
    if result.returncode:
        raise OSError(f"curl exit {result.returncode}: " + result.stderr.decode(errors="replace"))
    lines = result.stdout.decode().splitlines()
    if len(lines) != 2 or not lines[0].isdigit():
        raise ValueError("Missing response identity")
    status = int(lines[0])
    body = body_path.read_bytes()
    if len(body) > 5_000_000:
        raise ValueError("Response exceeds size bound")
    return {
        "status": status,
        "final_url": lines[1],
        "headers": parse_headers(header_path.read_bytes(), status),
        "body_sha256": sha(body),
        "body_file": str(body_path),
        "header_file": str(header_path),
    }, body


def header_policy(site: dict[str, bytes]) -> dict[str, str]:
    policy = {}
    for line in site["_headers"].decode().splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key in HEADER_NAMES:
            if key.lower() in policy:
                raise ValueError("Ambiguous header policy")
            policy[key.lower()] = value.strip()
    if len(policy) != len(HEADER_NAMES):
        raise ValueError("Incomplete header policy")
    return policy


def qualify(name: str, url: str, response: dict, body: bytes, expected: bytes, policy: dict) -> dict:
    problems = []
    if response.get("status") != 200 or response.get("final_url") != url:
        problems.append("status_or_route_mismatch")
    headers = response.get("headers", {})
    for key, value in policy.items():
        if headers.get(key) != [value]:
            problems.append("header:" + key)
    mime = headers.get("content-type", [])
    allowed = MIME.get(Path(name).suffix, {"application/json"} if name == ".well-known/mcp-server-card" else set())
    if len(mime) != 1 or mime[0].split(";", 1)[0].strip().lower() not in allowed:
        problems.append("mime_mismatch_or_unknown")
    exact = body == expected
    transformed = not exact and name.endswith(".html") and email_equivalent(body, expected)
    if not exact and not transformed:
        problems.append("content_mismatch")
    return {
        "file": name,
        "url": url,
        **response,
        "expected_sha256": sha(expected),
        "raw_exact": exact,
        "bounded_email_transform": transformed,
        "problems": problems,
        "state": "PASS" if not problems else "FAIL",
    }


def audit(site: dict[str, bytes], bases: list[str], output: Path, fetcher=fetch) -> dict:
    if not site or REQUIRED - site.keys() or not bases or len(bases) != len(set(bases)):
        raise ValueError("Empty, duplicate or incomplete audit scope")
    bases = [validate_base(base) for base in bases]
    if len(bases) != len(set(bases)):
        raise ValueError("Duplicate normalized host")
    policy = header_policy(site)
    jobs = [
        (base, name, data)
        for base in bases
        for name, data in sorted(site.items())
        if name not in {"_headers", "_redirects"}
    ]

    def inspect(job):
        base, name, expected = job
        url = base + route(name)
        try:
            response, body = fetcher(url, output)
            return qualify(name, url, response, body, expected, policy)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return {"file": name, "url": url, "state": "UNKNOWN", "error": str(exc)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(inspect, jobs))
    return {
        "state": "PASS" if rows and all(row["state"] == "PASS" for row in rows) else "NOT_ACCEPTED",
        "requested": len(jobs),
        "observed": len(rows),
        "passed": sum(row["state"] == "PASS" for row in rows),
        "failed": sum(row["state"] == "FAIL" for row in rows),
        "unknown": sum(row["state"] == "UNKNOWN" for row in rows),
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--commit")
    parser.add_argument("--base-url", action="append")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    report = {"state": "UNKNOWN", "started_utc": datetime.now(timezone.utc).isoformat()}
    output = None
    created_output = False
    try:
        if args.source_only:
            report.update(state="PASS", facts=source_check(ROOT), scope="source consistency only; no public requests")
        else:
            if not args.commit or not args.base_url or not args.output_dir:
                raise ValueError("Supply --commit, --base-url and a new --output-dir under internal/")
            output = args.output_dir.resolve()
            if not output.is_relative_to(ROOT / "internal") or output == ROOT / "internal":
                raise ValueError("Evidence output must be a new subdirectory under internal/")
            output.mkdir(parents=True, exist_ok=False)
            created_output = True
            site = committed_site(ROOT, args.commit)
            facts = source_check(ROOT)
            if json.loads(site["data/product-facts.json"]) != facts:
                raise ValueError("Committed facts disagree with canonical source")
            report.update(commit=args.commit, facts=facts, **audit(site, args.base_url, output))
            require_candidate(ROOT, args.commit)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        report.update(state="UNKNOWN", error=str(exc))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    if created_output:
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["state"] == "PASS" else 2 if report["state"] == "UNKNOWN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
