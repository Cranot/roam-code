"""Keep the documented network boundary synchronized with executable producers."""

from __future__ import annotations

from pathlib import Path

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
SRC = ROOT / "src" / "roam"
BOUNDARY_DOC = ROOT / "docs" / "network-boundary.md"


def _python_files_containing(*needles: str) -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            found.add(path.relative_to(ROOT).as_posix())
    return found


def test_direct_http_producers_are_audited() -> None:
    assert _python_files_containing(
        "urllib.request.urlopen(",
        "urllib.request.build_opener(",
    ) == {
        "src/roam/commands/cmd_metrics_push.py",
        "src/roam/commands/cmd_stale_refs.py",
        "src/roam/commands/cmd_version.py",
        "src/roam/github_check.py",
    }


def test_listener_and_loopback_producers_are_audited() -> None:
    assert _python_files_containing("ThreadingHTTPServer(") == {
        "src/roam/commands/cmd_watch.py",
    }
    assert _python_files_containing("socket.create_connection(") == {
        "src/roam/commands/cmd_compile_daemon.py",
        "src/roam/commands/cmd_hooks.py",
    }


def test_external_cli_producers_remain_explicit() -> None:
    producer_markers = {
        "src/roam/commands/cmd_pr_analyze.py": '["gh", "pr", "diff"',
        "src/roam/evidence/github_reviews.py": 'gh_executable: str = "gh"',
        "src/roam/attest/cga.py": '"cosign",\n        "sign-blob"',
    }
    for relative, marker in producer_markers.items():
        assert marker in (ROOT / relative).read_text(encoding="utf-8")


def test_boundary_doc_names_every_builtin_trigger_class() -> None:
    text = BOUNDARY_DOC.read_text(encoding="utf-8").casefold()
    required = (
        "tree-sitter-language-pack",
        "roam version --check",
        "roam metrics-push",
        "roam guard-pr --post-check",
        "roam pr-analyze --diff-from-pr",
        "roam pr-replay --github-reviews-gh",
        "roam stale-refs --check-external",
        "roam cga emit --sign --keyless",
        "roam index-export --sign --keyless",
        "roam pr-bundle emit --slsa-l3 --sign --keyless",
        "roam mcp --transport",
        "roam watch --webhook-port",
        "roam compile-daemon start",
        "roam verify",
    )
    for phrase in required:
        assert phrase in text, f"network-boundary inventory is missing {phrase!r}"


def test_network_boundary_is_linked_from_primary_public_surfaces() -> None:
    linked_files = (
        "README.md",
        "llms-install.md",
        "templates/distribution/landing-page/security.html",
        "templates/distribution/landing-page/privacy.html",
        "templates/distribution/landing-page/trust.html",
        "templates/legal/security-procurement-packet.md",
    )
    for relative in linked_files:
        text = (ROOT / Path(relative)).read_text(encoding="utf-8")
        assert "network-boundary.md" in text, f"{relative} does not link the canonical network inventory"
