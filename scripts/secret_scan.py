#!/usr/bin/env python
"""Pre-push secret scan over the commits being pushed.

This script reuses roam's repo-local secret patterns and adds a small
client-side safety net for pushed history. It scans each commit in a rev
range, inspects the files changed by that commit, and blocks on any
credential-shaped line unless the line is explicitly allowlisted.

Allowlist options:
- A line ending in an explicit ``# secretsallow``, ``// secretsallow``, or
  ``; secretsallow`` comment skips that line.
- A repo-root ``.secretsallow`` file can list path globs to skip whole files.

NOTE ON TEST FIXTURES: this scanner deliberately does NOT import
``roam.index.file_roles.is_test`` or otherwise suppress findings by file
role. ``cmd_secrets.scan_project`` (the ``roam secrets`` command) suppresses
test/fixture/docs paths by default because it is a developer-facing triage
tool; this script is the pre-push leak *gate* -- its entire job is to catch
mistakes regardless of which file they land in, and "it's a test file" must
not be a blanket licence to ship a real credential. A test file is exactly
where a developer is most likely to paste a real token "just for this one
run". Precision fixes belong in the pattern/value logic below, not in a
role-based bypass.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

from roam.commands.cmd_secrets import (
    _COMPILED_PATTERNS,
    _REMEDIATION,
    _high_entropy_passes,
    mask_secret,
)

_ALLOWLIST_RE = re.compile(r"(?i)(?:#|//|;)\s*secretsallow\s*$")


def _load_prepush_refs_module():
    module_path = Path(__file__).resolve().parent / "prepush_refs.py"
    spec = importlib.util.spec_from_file_location("roam_prepush_refs", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load pre-push parser from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_prepush_refs = _load_prepush_refs_module()


def _repo_root() -> Path:
    proc = subprocess.run(
        ["git", "--no-replace-objects", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit("not inside a git repository")
    try:
        root = proc.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise SystemExit("git repository root is not valid UTF-8") from exc
    if not root:
        raise SystemExit("not inside a git repository")
    return Path(root)


def _git_bytes(repo_root: Path, args: list[str], *, operation: str) -> bytes:
    """Run Git without locale-dependent text decoding and fail closed."""
    proc = subprocess.run(
        ["git", "--no-replace-objects", *args],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise SystemExit(f"{operation} failed (git exit {proc.returncode}){suffix}")
    return proc.stdout


def _decode_git_text(data: bytes, *, operation: str, encoding: str, errors: str = "strict") -> str:
    try:
        return data.decode(encoding, errors=errors)
    except UnicodeDecodeError as exc:
        raise SystemExit(f"{operation} produced invalid {encoding} output") from exc


def _git_nul_list(repo_root: Path, args: list[str], *, operation: str) -> list[str]:
    raw = _git_bytes(repo_root, args, operation=operation)
    text = _decode_git_text(raw, operation=operation, encoding="utf-8")
    return [part for part in text.split("\0") if part]


def _parse_path_allowlist(text: str) -> list[str]:
    patterns: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _load_path_allowlist_for_commit(repo_root: Path, commit: str) -> list[str]:
    entries_raw = _git_bytes(
        repo_root,
        ["ls-tree", "-z", commit, "--", ".secretsallow"],
        operation=f"locate allowlist for {commit}",
    )
    entries = [entry for entry in entries_raw.split(b"\0") if entry]
    if not entries:
        return []
    if len(entries) != 1:
        raise SystemExit(f"unexpected allowlist tree entries for {commit}")
    header, separator, path_raw = entries[0].partition(b"\t")
    fields = header.split(b" ")
    if not separator or len(fields) != 3:
        raise SystemExit(f"malformed allowlist tree entry for {commit}")
    mode_raw, object_type_raw, object_oid_raw = fields
    try:
        mode = mode_raw.decode("ascii", errors="strict")
        object_type = object_type_raw.decode("ascii", errors="strict")
        object_oid = object_oid_raw.decode("ascii", errors="strict")
        path = path_raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"undecodable allowlist tree entry for {commit}") from exc
    if mode not in {"100644", "100755"} or object_type != "blob" or path != ".secretsallow":
        raise SystemExit(f"allowlist must be a regular committed file for {commit}")
    allowlist_raw = _git_bytes(
        repo_root,
        ["cat-file", "blob", object_oid],
        operation=f"read allowlist for {commit}",
    )
    allowlist_text = _decode_git_text(
        allowlist_raw,
        operation=f"read allowlist for {commit}",
        encoding="utf-8",
    )
    return _parse_path_allowlist(allowlist_text)


def _path_is_allowlisted(rel_path: str, allowlist: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel_path, pattern) for pattern in allowlist)


# The scanner's OWN test fixture corpus: a path allowlist, not a directory
# rule, so it can't quietly grow into "tests/ is exempt" (which would
# reopen the coverage gap this scanner exists to close). A secret scanner
# cannot be tested without planting secret-shaped strings, so these two
# files matching the very patterns they exist to exercise is a structural
# certainty, not a finding -- not a loophole a real leak could hide behind,
# since these are named paths, not a directory rule. Ported from
# compile-code's scripts/secret_scan.py (commit ee7896e), which in turn
# cites this repo's own ``WHITELIST_FILES`` in
# scripts/internal_language_patterns.py as precedent for the same idiom.
_OWN_TEST_CORPUS_FILES = frozenset(
    {
        "tests/test_secrets_v2.py",
        "tests/test_secrets_ai_provider_keys.py",
    }
)


def _is_own_test_corpus(rel_path: str) -> bool:
    return rel_path.replace("\\", "/") in _OWN_TEST_CORPUS_FILES


# Ordinary tests -- NOT this scanner's fixture corpus -- whose synthetic
# credentials are already committed, unmarked, in commits that predate a
# later commit which added a `# secretsallow` line marker at the tip. This
# range gate reads each historical commit's OWN blob, so a tip-side marker
# never retroactively cleans an earlier commit that shipped without one
# (same lesson compile-code hit: a marker added at commit N+1 must not be
# trusted to clean commit N's full blob). This branch does not rewrite
# already-committed history to fix a scanner false positive, so the only
# option left for these two is a path exemption.
#
# Each entry below carries its OWN reason and is deliberately NOT folded
# into ``_OWN_TEST_CORPUS_FILES``, which asserts something these files are
# not -- the scanner's own fixture corpus. A dict (not a frozenset) so the
# rationale lives next to the path it excuses, not in a shared comment.
_LEGACY_FIXTURE_EXEMPTIONS: dict[str, str] = {
    "tests/test_hooks_claude_setup.py": (
        "test_prompt_turn_sequence_is_monotonic_and_prompt_private plants a "
        "synthetic 'private marker' string solely to prove Stop-hook "
        "telemetry never echoes prompt text verbatim. The value carries no "
        "vendor shape and is not a real credential; renaming it would not "
        "change that, and the commit that introduced it (pre-dating the "
        "tip-side `# secretsallow` marker) is already merged history."
    ),
    "tests/test_evidence_pr_replay.py": (
        "test_pr_replay_synth_bundle_scrubs_actor_secrets plants a 40-char "
        "GitHub-PAT-shaped literal specifically because the test proves the "
        "actor-scrub redaction path strips real-looking tokens -- reshaping "
        "the value into a placeholder would defeat the test it exists to "
        "run. The commit that introduced it (pre-dating the tip-side "
        "`# secretsallow` marker) is already merged history."
    ),
}


def _is_legacy_fixture_exemption(rel_path: str) -> bool:
    return rel_path.replace("\\", "/") in _LEGACY_FIXTURE_EXEMPTIONS


def _is_exempt_fixture_path(rel_path: str) -> bool:
    return _is_own_test_corpus(rel_path) or _is_legacy_fixture_exemption(rel_path)


def _line_is_allowlisted(line: str) -> bool:
    return _ALLOWLIST_RE.search(line) is not None


# The AWS-shaped entries are split across a concatenation deliberately. They are
# 20-char placeholder VALUES this scanner matches by equality, so they cannot be
# renamed — but a literal AKIA-prefixed token in source is itself credential-
# shaped, and `roam secrets --fail-on-found` flagged exactly these lines (the
# detector reading its own allowlist). Python folds the concatenation at compile
# time, so the members stay byte-identical. Same idiom as
# tests/test_secret_scan_hook.py. Do not re-inline these.
_EXACT_PLACEHOLDERS = frozenset(
    {
        "AKIA" + "IOSFODNN7EXAMPLE",
        "AKIA" + "IOSFODNN7TESTDAT",
        "AKIA" + "IOSFODNN7DOCEXAM",
        "changeme",
        "dummy",
        "example",
        "fake",
        "fixme",
        "placeholder",
        "replace_me",
        "sample",
        "todo",
    }
)


def _placeholder_candidate(match: re.Match[str]) -> str:
    """Extract the credential value, excluding assignment-key context."""
    if match.lastindex:
        return match.group(match.lastindex)
    matched = match.group()
    quoted = re.search(r"['\"]([^'\"]+)['\"]\s*$", matched)
    return quoted.group(1) if quoted else matched


# An un-interpolated f-string/``str.format``/shell-style placeholder token
# (``{name}``, ``${name}``, ``%(name)s``) is template SYNTAX, not data -- no
# vendor pattern above can ever legitimately match one (every vendor shape
# requires a fixed prefix or character set a bare placeholder can't
# produce), so this is safe to treat as a placeholder for every pattern,
# not just the generic ones. Ported from compile-code's
# scripts/secret_scan.py (commit ee7896e), same repo family, same problem:
# a range gate reading an un-interpolated f-string assignment fixture in a
# parametrized test as literal data.
_TEMPLATE_PLACEHOLDER_RE = re.compile(
    r"\{[A-Za-z_][A-Za-z0-9_]*\}|\$\{[A-Za-z_][A-Za-z0-9_]*\}|%\([A-Za-z_][A-Za-z0-9_]*\)s"
)

# Patterns whose regex is keyed on a variable NAME ("secret", "token",
# "password", ...) followed by "= '<anything 8+ chars>'" -- i.e. patterns
# that say nothing about the VALUE's shape, unlike e.g. "AWS Access Key"
# where the match *is* the credential. Only these get the identifier-vs-
# value check in ``_assignment_value_is_credential_shaped`` below; vendor
# patterns must NOT, since some real credentials (AWS key IDs) are
# themselves canonically all-uppercase-and-digits.
_GENERIC_ASSIGNMENT_PATTERNS = frozenset({"Generic Secret Assignment", "Generic Password Assignment"})

# A bare SCREAMING_SNAKE_CASE token, e.g. the NAME of an env var or a
# GitHub Actions secret to read at runtime.
_SCREAMING_SNAKE_IDENTIFIER_RE = re.compile(r"[A-Z][A-Z0-9_]*")


def _is_explicit_placeholder(match: re.Match[str]) -> bool:
    """Recognize whole placeholder values without substring exemptions."""
    raw = _placeholder_candidate(match).strip()
    if _TEMPLATE_PLACEHOLDER_RE.fullmatch(raw):
        return True
    value = raw.strip("<>{}[]()'\"")
    lower = value.lower()
    if value in _EXACT_PLACEHOLDERS or lower in _EXACT_PLACEHOLDERS:
        return True
    if re.fullmatch(r"[xX]{6,}", value):
        return True
    return re.fullmatch(r"(?:your|insert|replace)[_-][a-z0-9_-]+", lower) is not None


def _assignment_value_is_credential_shaped(pat: dict, match: re.Match[str]) -> bool:
    """Reject a "Generic *Assignment" hit whose quoted value is itself a bare
    SCREAMING_SNAKE_CASE identifier -- the NAME of an env var, GitHub
    secret, or constant being assigned, not an opaque credential value.
    Real credentials have entropy and character-class diversity; an
    all-caps-underscore token has neither. Scoped to the generic assignment
    patterns only: some vendor patterns (AWS Access Key IDs) are themselves
    canonically all-uppercase and must not be exempted by this rule. Ported
    from compile-code's scripts/secret_scan.py (commit ee7896e).
    """
    if pat["name"] not in _GENERIC_ASSIGNMENT_PATTERNS:
        return True
    value = _placeholder_candidate(match)
    return _SCREAMING_SNAKE_IDENTIFIER_RE.fullmatch(value) is None


def _decode_content_bytes(data: bytes, *, operation: str) -> str:
    """Decode text bodies while preserving ASCII findings after bad bytes."""
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return _decode_git_text(data, operation=operation, encoding="utf-32", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return _decode_git_text(data, operation=operation, encoding="utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return _decode_git_text(data, operation=operation, encoding="utf-8-sig", errors="replace")
    return _decode_git_text(data, operation=operation, encoding="utf-8", errors="replace")


def _redact_label(label: str) -> str:
    """Mask credential-shaped values before a label reaches diagnostics."""
    redacted = label
    for pat in _COMPILED_PATTERNS:
        redacted = pat["regex"].sub(lambda match: mask_secret(match.group()), redacted)
    return redacted


def _scan_text(
    rel_path: str,
    text: str,
    *,
    commit: str,
    allow_suppression: bool = True,
) -> list[dict]:
    findings: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if allow_suppression and _line_is_allowlisted(line):
            continue
        for pat in _COMPILED_PATTERNS:
            for match in pat["regex"].finditer(line):
                matched_value = match.group()
                if _is_explicit_placeholder(match):
                    continue
                if not _high_entropy_passes(pat, match):
                    continue
                if not _assignment_value_is_credential_shaped(pat, match):
                    continue
                findings.append(
                    {
                        "file": _redact_label(rel_path),
                        "line": line_no,
                        "severity": pat["severity"],
                        "pattern_name": pat["name"],
                        "matched_text": mask_secret(matched_value),
                        "remediation": _REMEDIATION.get(pat["name"], "Move to environment variable or secrets manager"),
                        "commit": commit,
                    }
                )
    return findings


def _resolve_commits(repo_root: Path, revision_args: str | tuple[str, ...]) -> list[str]:
    args = (revision_args,) if isinstance(revision_args, str) else revision_args
    label = " ".join(args)
    commits_raw = _git_bytes(
        repo_root,
        ["rev-list", "--reverse", *args],
        operation=f"resolve revisions {label}",
    )
    commits = _decode_git_text(commits_raw, operation="rev-list", encoding="ascii")
    return [line.strip() for line in commits.splitlines() if line.strip()]


def _scan_commits(repo_root: Path, commits: list[str]) -> list[dict]:
    findings: list[dict] = []
    for commit in commits:
        try:
            commit_raw = _prepush_refs.read_commit_object(repo_root, commit)
        except _prepush_refs.PrePushGitError as exc:
            raise SystemExit(f"cannot inspect commit object: {exc}") from exc
        commit_text = _decode_content_bytes(
            commit_raw,
            operation=f"decode commit object {commit}",
        )
        findings.extend(
            _scan_text(
                f"{commit[:7]} (commit object)",
                commit_text,
                commit=commit,
                allow_suppression=False,
            )
        )
        path_allowlist = _load_path_allowlist_for_commit(repo_root, commit)
        changed_paths = _git_nul_list(
            repo_root,
            [
                "diff-tree",
                "--root",
                "-r",
                "-m",
                "--no-commit-id",
                "--name-only",
                "-z",
                "--diff-filter=d",
                commit,
            ],
            operation=f"list changed paths for {commit}",
        )
        for rel_path in dict.fromkeys(changed_paths):
            findings.extend(
                _scan_text(
                    f"{commit[:7]}:{rel_path} (published path name)",
                    rel_path,
                    commit=commit,
                    allow_suppression=False,
                )
            )
            if _path_is_allowlisted(rel_path, path_allowlist) or _is_exempt_fixture_path(rel_path):
                continue
            blob_raw = _git_bytes(
                repo_root,
                ["show", f"{commit}:{rel_path}"],
                operation=f"read blob {commit}:{rel_path}",
            )
            blob = _decode_content_bytes(
                blob_raw,
                operation=f"read blob {commit}:{rel_path}",
            )
            findings.extend(_scan_text(rel_path, blob, commit=commit))
    findings.sort(key=lambda row: (row["commit"], row["file"], row["line"], row["pattern_name"]))
    return findings


def scan_commit_range(repo_root: Path, rev_range: str) -> list[dict]:
    """Scan every commit in *rev_range* and return secret findings."""
    return _scan_commits(repo_root, _resolve_commits(repo_root, rev_range))


def scan_pre_push_updates(
    repo_root: Path,
    updates_path: Path,
    *,
    remote_url: str | None = None,
) -> list[dict]:
    """Scan every commit introduced by captured Git pre-push updates."""
    try:
        updates = _prepush_refs.load_pre_push_updates(updates_path)
    except _prepush_refs.PrePushUpdateError as exc:
        raise SystemExit(f"invalid pre-push updates: {exc}") from exc

    findings: list[dict] = []
    try:
        direct_texts = _prepush_refs.collect_direct_published_texts(repo_root, updates)
    except _prepush_refs.PrePushGitError as exc:
        raise SystemExit(f"cannot inspect pushed objects: {exc}") from exc
    for published in direct_texts:
        text = _decode_content_bytes(
            published.data,
            operation=f"decode pushed {published.kind} {published.oid}",
        )
        findings.extend(
            _scan_text(
                published.path,
                text,
                commit=published.oid,
                allow_suppression=published.kind == "blob",
            )
        )

    published_commits: tuple[str, ...] = ()
    if remote_url is not None and any(update.is_new_remote_ref and not update.is_deletion for update in updates):
        try:
            published_commits = _prepush_refs.authoritative_remote_commits(repo_root, remote_url)
        except (_prepush_refs.PrePushUpdateError, _prepush_refs.PrePushGitError) as exc:
            raise SystemExit(f"cannot verify destination history: {exc}") from exc

    commits: list[str] = []
    seen: set[str] = set()
    revision_groups = _prepush_refs.unique_revision_args(updates, published_commits=published_commits)
    for revision_args in revision_groups:
        for commit in _resolve_commits(repo_root, revision_args):
            if commit in seen:
                continue
            seen.add(commit)
            commits.append(commit)
    findings.extend(_scan_commits(repo_root, commits))
    findings.sort(key=lambda row: (row["commit"], row["file"], row["line"], row["pattern_name"]))
    return findings


def _format_findings(findings: list[dict]) -> str:
    lines = [f"BLOCKED: {len(findings)} secret finding(s) in pushed commits"]
    for finding in findings:
        lines.append(
            f"{finding['commit'][:7]} {finding['file']}:{finding['line']} "
            f"[{finding['pattern_name']}] {finding['matched_text']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan pushed commits for secret-shaped values.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--rev-range",
        help="Git rev range to scan (default: HEAD when no mode is given).",
    )
    mode.add_argument(
        "--pre-push-updates",
        type=Path,
        metavar="FILE",
        help="Captured Git pre-push ref-update stream to scan.",
    )
    parser.add_argument(
        "--remote-url",
        help="Exact push destination used to verify already-published history.",
    )
    args = parser.parse_args(argv)
    if args.remote_url is not None and args.pre_push_updates is None:
        parser.error("--remote-url requires --pre-push-updates")

    repo_root = _repo_root()
    if args.pre_push_updates is not None:
        findings = scan_pre_push_updates(repo_root, args.pre_push_updates, remote_url=args.remote_url)
    else:
        findings = scan_commit_range(repo_root, args.rev_range or "HEAD")
    if findings:
        print(_format_findings(findings), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
