"""Hermetic production acceptance controls; no real HTTP requests or clock waits."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import verify_site_deployment as check
from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SITE = ROOT / check.SITE


def fixture_response(name, body, policy=None):
    suffix = Path(name).suffix
    mime = sorted(check.MIME.get(suffix, {"application/json"}))[0]
    return {
        "status": 200,
        "final_url": "https://roam-code.com" + check.route(name),
        "headers": {"content-type": [mime], **{k: [v] for k, v in (policy or {}).items()}},
    }


def test_real_source_shape_and_current_facts():
    facts = check.facts_builder.product_facts(ROOT)
    files = [p for p in SITE.rglob("*") if p.suffix in {".html", ".txt", ".md"}]
    assert len(files) > 30
    issues = [
        issue
        for path in files
        for issue in check.current_fact_issues(
            path.relative_to(SITE).as_posix(), path.read_text(encoding="utf-8"), facts
        )
    ]
    assert issues == []


@pytest.mark.parametrize(
    "phrase",
    [
        "244 MCP tools",
        "245 MCP tools",
        "275 CLI commands",
        "268 CLI commands",
        "16 default core tools",
        "16 core tools",
        "current: v13.2.0",
        "current version: v14.0.4",
    ],
)
def test_stale_current_fact_refused_but_dated_archive_retained(phrase):
    facts = check.facts_builder.product_facts(ROOT)
    assert check.current_fact_issues("press.html", phrase, facts)
    assert not check.current_fact_issues("changelog.html", phrase, facts)
    assert not check.current_fact_issues("measurements.html", "Historical experiment, engine v13.10.0", facts)


@pytest.mark.parametrize("mutation", ["price", "count", "availability", "extra", "empty", "old_html"])
def test_served_mutations_cannot_agree_themselves_into_a_pass(mutation):
    expected = (SITE / "data/product-facts.json").read_bytes()
    facts = json.loads(expected)
    if mutation == "price":
        facts["teamReplayPrice"] -= 100
    elif mutation == "count":
        facts["mcpToolCount"] -= 1
    elif mutation == "availability":
        facts["productAvailability"]["review"] = "AVAILABLE"
    body = json.dumps(facts).encode()
    if mutation == "extra":
        body = expected + b" \n"
    elif mutation == "empty":
        body = b""
    elif mutation == "old_html":
        body = b"<html>Old deployment</html>"
    row = check.qualify(
        "data/product-facts.json",
        "https://roam-code.com/data/product-facts.json",
        fixture_response("data/product-facts.json", body),
        body,
        expected,
        {},
    )
    assert row["state"] == "FAIL" and "content_mismatch" in row["problems"]


def test_historical_bytes_are_valid_without_replacing_old_versions():
    body = b"<h2>2025 release v13.0.0</h2><p>245 MCP tools, 16 core tools</p>"
    row = check.qualify(
        "changelog.html", "https://roam-code.com/changelog", fixture_response("changelog.html", body), body, body, {}
    )
    assert row["state"] == "PASS"


def encoded(email=b"hello@roam-code.com"):
    return bytes([53, *(v ^ 53 for v in email)]).hex().encode()


def transformed(email=b"hello@roam-code.com"):
    return (
        b'<p><a href="mailto:hello@roam-code.com"><span class="__cf_email__" data-cfemail="'
        + encoded(email)
        + b'">[email&#160;protected]</span></a></p>'
        + check.EMAIL_SCRIPT
    )


def test_email_transform_is_narrow_not_generic_html_normalization():
    expected = b'<p><a href="mailto:hello@roam-code.com">hello@roam-code.com</a></p>'
    actual = transformed()
    assert check.email_equivalent(actual, expected)
    for bad in (
        actual + b" ",
        actual + check.EMAIL_SCRIPT,
        actual.replace(b"<p>", b"<p hidden>"),
        transformed(b"wrong@roam-code.com"),
        actual.replace(b"data-cfemail", b"data-other"),
        actual.replace(b"email-decode.min.js", b"different.js"),
        b"",
        expected,
    ):
        assert not check.email_equivalent(bad, expected)
    mailto = b'<a href="/cdn-cgi/l/email-protection#' + encoded() + b'">Email</a>' + check.EMAIL_SCRIPT
    assert check.email_equivalent(mailto, b'<a href="mailto:hello@roam-code.com">Email</a>')


@pytest.mark.parametrize("damage", ["status", "url", "mime", "duplicate_header", "cache", "missing_header"])
def test_headers_and_identity_are_not_washed_away_by_matching_body(damage):
    policy = {"cache-control": "public, max-age=0, must-revalidate"}
    body = b"body{}"
    response = fixture_response("landing.css", body, policy)
    if damage == "status":
        response["status"] = 403
    elif damage == "url":
        response["final_url"] = "https://roam-code.com/"
    elif damage == "mime":
        response["headers"]["content-type"] = ["text/html"]
    elif damage == "duplicate_header":
        response["headers"]["cache-control"] *= 2
    elif damage == "cache":
        response["headers"]["cache-control"] = ["public, max-age=14400, must-revalidate"]
    else:
        response["headers"].pop("cache-control")
    assert (
        check.qualify("landing.css", "https://roam-code.com/landing.css", response, body, body, policy)["state"]
        == "FAIL"
    )


def test_all_file_inventory_and_partial_transport_accounting(tmp_path):
    site = {name: (SITE / name).read_bytes() for name in check.REQUIRED}
    policy = check.header_policy(site)
    by_url = {"https://roam-code.com" + check.route(name): name for name in site}

    def good(url, output):
        name = by_url[url]
        return fixture_response(name, site[name], policy), site[name]

    result = check.audit(site, ["https://roam-code.com"], tmp_path, good)
    assert result["state"] == "PASS"
    assert result["requested"] == result["observed"] == result["passed"] == len(site) - 2

    def partial(url, output):
        if url.endswith("/pricing"):
            raise OSError("timeout")
        return good(url, output)

    result = check.audit(site, ["https://roam-code.com"], tmp_path, partial)
    assert result["state"] == "NOT_ACCEPTED" and result["unknown"] == 1
    assert result["passed"] == len(site) - 3
    for bad_site, bases in (
        ({}, ["https://roam-code.com"]),
        (site, []),
        (site, ["https://roam-code.com", "https://roam-code.com/"]),
    ):
        with pytest.raises(ValueError):
            check.audit(bad_site, bases, tmp_path, good)


@pytest.mark.parametrize(
    "url",
    [
        "http://roam-code.com",
        "https://roam-code.com.attacker.test",
        "https://user:password@roam-code.com",
        "https://roam-code.com/?q=x",
        "https://127.0.0.1",
        "https://roam-code.pages.dev",
    ],
)
def test_host_scope_is_closed(url):
    with pytest.raises(ValueError):
        check.validate_base(url)


def test_http_parser_keeps_duplicates_and_final_proxy_status():
    headers = check.parse_headers(
        b"HTTP/1.1 200 Connection established\r\n\r\nHTTP/2 403\r\nX-A: a\r\nX-A: b\r\n\r\n", 403
    )
    assert headers == {"x-a": ["a", "b"]}
    for raw in (b"", b"invalid", b"HTTP/2 200\r\nX-A: a\r\n\r\n"):
        with pytest.raises(ValueError):
            check.parse_headers(raw, 403)


def test_existing_output_directory_is_untouched(tmp_path, monkeypatch):
    output = tmp_path / "internal/kept"
    output.mkdir(parents=True)
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(
        check.sys,
        "argv",
        ["check", "--commit", "a" * 40, "--base-url", "https://roam-code.com", "--output-dir", str(output)],
    )
    assert check.main() == 2
    assert list(output.iterdir()) == []


def test_curl_transport_records_real_response_shape_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: "curl")

    def run(argv, **kwargs):
        assert "--insecure" not in argv and "--location" not in argv
        assert argv[argv.index("--proto") + 1] == "=https"
        assert kwargs["timeout"] == 35
        Path(argv[argv.index("--dump-header") + 1]).write_bytes(b"HTTP/2 200\r\nContent-Type: text/plain\r\n\r\n")
        Path(argv[argv.index("--output") + 1]).write_bytes(b"fixture")
        return subprocess.CompletedProcess(argv, 0, b"200\nhttps://roam-code.com/llms.txt\n", b"")

    monkeypatch.setattr(check.subprocess, "run", run)
    response, body = check.fetch("https://roam-code.com/llms.txt", tmp_path)
    assert body == b"fixture" and response["body_sha256"] == check.sha(body)
    assert Path(response["body_file"]).read_bytes() == body
    assert response["headers"] == {"content-type": ["text/plain"]}


@pytest.mark.parametrize(
    "damage", ["dirty", "untracked", "wrong_head", "missing", "export-ignore", "export-subst", "none"]
)
def test_committed_inventory_boundary_with_real_isolated_git(tmp_path, damage):
    # Git is the boundary under test: isolated disposable repo, no remote or credentials.
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, timeout=15).stdout

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    for name in check.REQUIRED:
        path = tmp_path / check.SITE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((SITE / name).read_bytes())
    site = tmp_path / check.SITE
    if damage == "missing":
        (site / "pricing.html").unlink()
    if damage.startswith("export-"):
        (site / "index.html").write_bytes(b"$Format:%H$\n")
        (site / ".gitattributes").write_text("index.html " + damage + "\n", encoding="utf-8")
    git("add", ".")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "Fixture",
    )
    commit = git("rev-parse", "HEAD").decode().strip()
    if damage == "dirty":
        (site / "index.html").write_bytes(b"changed")
    elif damage == "untracked":
        (tmp_path / "unknown.txt").write_bytes(b"unreviewed")
    elif damage == "wrong_head":
        commit = "0" * 40
    if damage == "none":
        exported = check.committed_site(tmp_path, commit)
        assert set(exported) == check.REQUIRED
        assert all(data == (site / name).read_bytes() for name, data in exported.items())
    else:
        with pytest.raises(ValueError):
            check.committed_site(tmp_path, commit)
