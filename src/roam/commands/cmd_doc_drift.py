"""Verify concrete prose-documentation claims against repository authorities.

Output formats: text (default), ``--json``, and SARIF. SARIF is emitted
because every drift finding has an exact Markdown file and line location.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from roam.capability import roam_capability
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.output.formatter import format_table, json_envelope, to_json

_INLINE_CODE_RE = re.compile(r"(?<!`)`(?P<value>[^`\n]+)`(?!`)")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,})(?P<tail>.*)$")
_KNOWN_PREFIX_PATH_RE = re.compile(r"^(?:src|tests|dev|scripts|templates|docs)/[A-Za-z0-9_./-]+$")
_EXTENSION_PATH_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)+\.[A-Za-z0-9]+$")
_COUNT_RE = re.compile(
    r"(?<![\w.])"
    r"(?:(?P<prefix>over|more\s+than|about|roughly)\s+)?"
    r"(?P<tilde>~)?(?P<number>\d[\d,]*)(?P<plus>\+)?"
    r"\s+(?P<first>[A-Za-z][A-Za-z-]*)"
    r"(?:\s+(?P<second>[A-Za-z][A-Za-z-]*))?",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"\bv?(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b", re.IGNORECASE)
_VERSION_TOKEN_RE = re.compile(
    r"\bversion\b|\bv?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\b|\b[A-Za-z0-9_+-]+\b",
    re.IGNORECASE,
)
_PLACEHOLDER_SEGMENTS = frozenset({"yourcommand", "yourlang", "example", "foo", "bar", "x"})
_RESOLVABLE_NOUNS = frozenset(
    {
        "file",
        "files",
        "function",
        "functions",
        "class",
        "classes",
        "symbol",
        "symbols",
        "language",
        "languages",
        "command",
        "commands",
    }
)


@dataclass(frozen=True)
class _Metric:
    value: int | None
    definition: str
    reason: str | None = None


class _GitIgnore:
    """Memoized ``git check-ignore`` authority for one repository."""

    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[str, bool | None] = {}

    def matches(self, relative_path: str) -> bool | None:
        normalized = relative_path.replace(os.sep, "/")
        if normalized in self.cache:
            return self.cache[normalized]
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--quiet", "--no-index", "--", normalized],
                cwd=self.root,
                capture_output=True,
                timeout=5,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            status = None
        else:
            status = True if result.returncode == 0 else False if result.returncode == 1 else None
        self.cache[normalized] = status
        return status


def _is_historical_doc(relative_path: str) -> bool:
    name = Path(relative_path).name
    lower_name = name.lower()
    if lower_name.startswith("changelog") and lower_name.endswith(".md"):
        return True
    normalized_stem = Path(lower_name).stem.replace("_", "-").replace(".", "-")
    return normalized_stem.startswith("release-notes") or normalized_stem.startswith("releasenotes")


def _discover_docs(root: Path, git_ignore: _GitIgnore) -> tuple[list[Path], list[str], list[str]]:
    docs: list[Path] = []
    walk_errors: list[str] = []
    ignore_unknown: list[str] = []

    def _onerror(exc: OSError) -> None:
        filename = Path(exc.filename).name if exc.filename else "unknown directory"
        walk_errors.append(f"could not scan {filename}: {exc.__class__.__name__}")

    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=_onerror):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            path = Path(directory) / filename
            relative = path.relative_to(root).as_posix()
            if _is_historical_doc(relative):
                continue
            ignored = git_ignore.matches(relative)
            if ignored is True:
                continue
            if ignored is None:
                ignore_unknown.append(relative)
            docs.append(path)
    docs.sort(key=lambda item: item.relative_to(root).as_posix())
    return docs, walk_errors, ignore_unknown


def _non_fenced_lines(text: str) -> list[tuple[int, str]]:
    output: list[tuple[int, str]] = []
    open_width = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        fence = _FENCE_RE.match(line)
        if open_width:
            if fence and len(fence.group("fence")) >= open_width and not fence.group("tail").strip():
                open_width = 0
            continue
        if fence:
            open_width = len(fence.group("fence"))
            continue
        output.append((line_number, line))
    return output


def _clean_bare_token(token: str) -> str:
    return token.strip("\"'()[],:;!").rstrip(".")


def _has_placeholder_segment(path: str) -> bool:
    for segment in path.split("/"):
        base = segment.rsplit(".", 1)[0].lower()
        if segment.lower() in _PLACEHOLDER_SEGMENTS or base in _PLACEHOLDER_SEGMENTS:
            return True
    return False


def _path_candidate(value: str) -> str | None:
    candidate = value.strip()
    if "://" in candidate or any(char in candidate for char in "*?<>{}"):
        return None
    candidate = candidate.replace("\\", "/")
    if candidate.startswith("./"):
        candidate = candidate[2:]
    if candidate.startswith("/") or not (
        _EXTENSION_PATH_RE.fullmatch(candidate) or _KNOWN_PREFIX_PATH_RE.fullmatch(candidate)
    ):
        return None
    if _has_placeholder_segment(candidate):
        return None
    return candidate


def _extract_path_claims(line: str, *, doc: str, line_number: int) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for match in _INLINE_CODE_RE.finditer(line):
        occupied.append(match.span())
        candidate = _path_candidate(match.group("value"))
        if candidate:
            claims.append(
                {
                    "doc": doc,
                    "line": line_number,
                    "kind": "path",
                    "claim_text": candidate,
                    "_path": candidate,
                }
            )

    bare_line = list(line)
    for start, end in occupied:
        bare_line[start:end] = " " * (end - start)
    for raw in "".join(bare_line).split():
        if "://" in raw:
            continue
        candidate = _path_candidate(_clean_bare_token(raw))
        if candidate:
            claims.append(
                {
                    "doc": doc,
                    "line": line_number,
                    "kind": "path",
                    "claim_text": candidate,
                    "_path": candidate,
                }
            )
    return claims


def _normalized_noun(token: str | None) -> str:
    noun = (token or "").lower()
    singulars = {
        "files": "file",
        "functions": "function",
        "classes": "class",
        "symbols": "symbol",
        "languages": "language",
        "commands": "command",
    }
    return singulars.get(noun, noun)


def _count_metric_key(first: str, second: str | None) -> tuple[str | None, int]:
    first_noun = _normalized_noun(first)
    second_noun = _normalized_noun(second)
    if first_noun in _RESOLVABLE_NOUNS:
        return first_noun, 1
    if second_noun in _RESOLVABLE_NOUNS:
        if first_noun in {"source", "test"} and second_noun in {"file", "files"}:
            return f"{first_noun} files", 2
        return second_noun, 2
    return None, 1


def _extract_count_claims(line: str, *, doc: str, line_number: int) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for match in _COUNT_RE.finditer(line):
        metric_key, token_count = _count_metric_key(match.group("first"), match.group("second"))
        end = match.end("second") if token_count == 2 else match.end("first")
        prefix = (match.group("prefix") or "").lower()
        if prefix in {"over", "more than"} or match.group("plus"):
            qualifier = "minimum"
        elif prefix in {"about", "roughly"} or match.group("tilde"):
            qualifier = "approximate"
        else:
            qualifier = "exact"
        claims.append(
            {
                "doc": doc,
                "line": line_number,
                "kind": "count",
                "claim_text": line[match.start() : end],
                "_number": int(match.group("number").replace(",", "")),
                "_qualifier": qualifier,
                "_metric_key": metric_key,
                "_noun": _normalized_noun(match.group("second") if token_count == 2 else match.group("first")),
            }
        )
    return claims


def _extract_version_claims(line: str, *, doc: str, line_number: int) -> list[dict[str, Any]]:
    tokens = list(_VERSION_TOKEN_RE.finditer(line))
    version_indexes = [index for index, token in enumerate(tokens) if _VERSION_RE.fullmatch(token.group(0))]
    keyword_indexes = [index for index, token in enumerate(tokens) if token.group(0).lower() == "version"]
    claims: list[dict[str, Any]] = []
    for version_index in version_indexes:
        nearby = [index for index in keyword_indexes if abs(index - version_index) - 1 <= 3]
        if not nearby:
            continue
        keyword_index = min(nearby, key=lambda index: abs(index - version_index))
        first = tokens[min(keyword_index, version_index)]
        last = tokens[max(keyword_index, version_index)]
        raw_version = tokens[version_index].group(0)
        claims.append(
            {
                "doc": doc,
                "line": line_number,
                "kind": "version",
                "claim_text": line[first.start() : last.end()],
                "_version": raw_version[1:] if raw_version.lower().startswith("v") else raw_version,
            }
        )
    return claims


def _extract_claims(text: str, relative_doc: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for line_number, line in _non_fenced_lines(text):
        claims.extend(_extract_path_claims(line, doc=relative_doc, line_number=line_number))
        claims.extend(_extract_count_claims(line, doc=relative_doc, line_number=line_number))
        claims.extend(_extract_version_claims(line, doc=relative_doc, line_number=line_number))
    return claims


def _commands_metric(conn, root: Path) -> _Metric:
    rows = conn.execute(
        """
        SELECT DISTINCT f.path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name = '_COMMANDS' AND f.language = 'python'
        """
    ).fetchall()
    candidate_paths = sorted({str(row["path"]) for row in rows})
    registries: list[int] = []
    for relative in candidate_paths:
        path = root / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeError, SyntaxError):
            continue
        assignments: list[ast.expr] = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_COMMANDS" for target in node.targets
            ):
                assignments.append(node.value)
            elif (
                isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_COMMANDS"
            ):
                assignments.append(node.value)
        if len(assignments) != 1 or not isinstance(assignments[0], ast.Dict):
            continue
        keys = assignments[0].keys
        if not all(isinstance(key, ast.Constant) and isinstance(key.value, str) for key in keys):
            continue
        registries.append(len(keys))

    definition = "string-key entries in the sole indexed module-level Python _COMMANDS dict literal"
    if len(registries) == 1:
        return _Metric(registries[0], definition)
    if not registries:
        return _Metric(None, definition, "no countable _COMMANDS registry was indexed")
    return _Metric(None, definition, "multiple countable _COMMANDS registries were indexed")


def _count_metrics(conn, root: Path) -> dict[str, _Metric]:
    query_specs = {
        "files": ("SELECT COUNT(*) FROM files", "all rows in the roam files index"),
        "source files": (
            "SELECT COUNT(*) FROM files WHERE COALESCE(file_role, 'source') = 'source'",
            "indexed files whose file_role is 'source'",
        ),
        "test files": (
            "SELECT COUNT(*) FROM files WHERE file_role = 'test'",
            "indexed files whose file_role is 'test'",
        ),
        "functions": (
            "SELECT COUNT(*) FROM symbols WHERE kind = 'function'",
            "indexed symbols whose kind is 'function'",
        ),
        "classes": (
            "SELECT COUNT(*) FROM symbols WHERE kind = 'class'",
            "indexed symbols whose kind is 'class'",
        ),
        "symbols": ("SELECT COUNT(*) FROM symbols", "all rows in the roam symbols index"),
        "languages": (
            "SELECT COUNT(DISTINCT language) FROM files WHERE language IS NOT NULL AND language != ''",
            "distinct non-empty language values in the roam files index",
        ),
    }
    metrics: dict[str, _Metric] = {}
    for key, (sql, definition) in query_specs.items():
        row = conn.execute(sql).fetchone()
        metrics[key] = _Metric(int(row[0]) if row else 0, definition)
    metrics["commands"] = _commands_metric(conn, root)
    return metrics


def _metric_for_claim(metric_key: str | None, metrics: dict[str, _Metric], noun: str) -> _Metric:
    if metric_key is None:
        return _Metric(None, f"no built-in metric is defined for noun '{noun}'", f"noun '{noun}' has no resolver")
    normalized = metric_key
    if normalized in {"file", "files"}:
        normalized = "files"
    elif normalized in {"function", "functions"}:
        normalized = "functions"
    elif normalized in {"class", "classes"}:
        normalized = "classes"
    elif normalized in {"symbol", "symbols"}:
        normalized = "symbols"
    elif normalized in {"language", "languages"}:
        normalized = "languages"
    elif normalized in {"command", "commands"}:
        normalized = "commands"
    return metrics[normalized]


def _evaluate_count_claim(claim: dict[str, Any], metrics: dict[str, _Metric]) -> dict[str, Any]:
    metric = _metric_for_claim(claim["_metric_key"], metrics, claim["_noun"])
    expected = claim["_number"]
    finding = {
        **claim,
        "expected": expected,
        "actual": metric.value,
        "metric_definition": metric.definition,
    }
    if metric.value is None:
        finding["status"] = "unverifiable"
        finding["reason"] = metric.reason or "count authority unavailable"
        finding["_authority_unavailable"] = True
        return finding

    qualifier = claim["_qualifier"]
    if qualifier == "minimum":
        agrees = metric.value >= expected
    elif qualifier == "approximate":
        agrees = abs(metric.value - expected) <= expected * 0.10
    else:
        agrees = metric.value == expected
    finding["status"] = "verified" if agrees else "drifted"
    return finding


def _evaluate_path_claim(claim: dict[str, Any], root: Path, git_ignore: _GitIgnore) -> dict[str, Any]:
    relative = claim["_path"]
    target = root / relative
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except (OSError, ValueError):
        return {
            **claim,
            "expected": "repo-relative path",
            "actual": "path escapes project root",
            "status": "unverifiable",
            "reason": "path does not resolve inside the project root",
        }
    ignored = git_ignore.matches(relative)
    if ignored is True:
        return {
            **claim,
            "expected": "path existence not checked",
            "actual": "gitignored path",
            "status": "unverifiable",
            "reason": "gitignored path is matched by git check-ignore",
        }
    if ignored is None:
        return {
            **claim,
            "expected": "path existence",
            "actual": None,
            "status": "unverifiable",
            "reason": "git check-ignore authority unavailable for missing path",
            "_authority_unavailable": True,
        }
    if target.exists():
        return {**claim, "expected": "path exists", "actual": "path exists", "status": "verified"}
    return {**claim, "expected": "path exists", "actual": "path missing", "status": "drifted"}


def _load_toml(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        except ImportError:
            return None, "TOML parser unavailable"
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle), None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, f"{path.name} metadata unreadable: {exc.__class__.__name__}"


def _version_authority(root: Path) -> tuple[str | None, str | None, str]:
    authorities: list[tuple[str, str]] = []
    errors: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data, error = _load_toml(pyproject)
        if error:
            errors.append(error)
        elif data is not None:
            project = data.get("project")
            version = project.get("version") if isinstance(project, dict) else None
            if isinstance(version, str) and version:
                authorities.append((version, "pyproject.toml [project].version"))
            elif isinstance(project, dict) and "version" in (project.get("dynamic") or []):
                errors.append("pyproject.toml declares a dynamic project version")

    package_json = root / "package.json"
    if package_json.is_file():
        try:
            package_data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"package.json metadata unreadable: {exc.__class__.__name__}")
        else:
            version = package_data.get("version") if isinstance(package_data, dict) else None
            if isinstance(version, str) and version:
                authorities.append((version, "package.json version"))

    if not authorities:
        reason = "; ".join(errors) if errors else "no static project version metadata was found"
        return None, None, reason
    distinct = {version for version, _source in authorities}
    if len(distinct) != 1:
        sources = ", ".join(source for _version, source in authorities)
        return None, None, f"project version authorities disagree across {sources}"
    return authorities[0][0], ", ".join(source for _version, source in authorities), ""


def _evaluate_version_claim(
    claim: dict[str, Any],
    authority_version: str | None,
    authority_name: str | None,
    authority_reason: str,
) -> dict[str, Any]:
    expected = claim["_version"]
    finding = {**claim, "expected": expected, "actual": authority_version, "authority": authority_name}
    if authority_version is None:
        finding["status"] = "unverifiable"
        finding["reason"] = authority_reason
        finding["_authority_unavailable"] = True
    else:
        finding["status"] = "verified" if expected == authority_version else "drifted"
    return finding


def _public_finding(finding: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in finding.items() if not key.startswith("_")}


def _verdict(docs_scanned: int, claims_total: int, verified: int, drifted: int, unverifiable: int, gate: bool) -> str:
    if docs_scanned == 0:
        if gate:
            return "Refused doc-drift gate — zero Markdown docs scanned"
        return "No Markdown docs scanned; documentation claims were not checked"
    if claims_total == 0:
        return f"{docs_scanned} Markdown docs scanned; zero claims extracted"
    if drifted:
        return f"{drifted} of {claims_total} documentation claims drifted across {docs_scanned} Markdown docs"
    if unverifiable:
        return (
            f"No objective doc drift: {verified} verified and {unverifiable} unverifiable claims "
            f"across {docs_scanned} Markdown docs"
        )
    return f"No objective doc drift: {verified} documentation claims verified across {docs_scanned} Markdown docs"


@roam_capability(
    name="doc-drift",
    category="refactoring",
    summary="Verify Markdown path, count, and version claims against mechanical repository authorities",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("review",),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
    displaces=("find Markdown plus grep for paths/counts and manual file/version checks",),
)
@click.command("doc-drift")
@click.option("--ci", is_flag=True, help="Fail when any objective drift is found; refuse when zero docs are scanned.")
@click.option(
    "--threshold",
    type=click.IntRange(min=0),
    default=None,
    help="Fail when drifted claims exceed this maximum; 0 requires zero drift.",
)
@click.pass_context
def doc_drift(ctx, ci: bool, threshold: int | None) -> None:
    """Verify concrete prose claims in Markdown docs against repository state.

    Extracts only path, count, and project-version claims, then compares them
    with the filesystem, roam index, and static project metadata. This
    displaces combining Bash ``find``, Grep claim searches, and repeated Read
    calls to check every documented path/count/version by hand.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    gate_enabled = bool(ci or threshold is not None)
    allowed_drift = threshold if threshold is not None else 0

    ensure_index()
    root = find_project_root()
    git_ignore = _GitIgnore(root)
    doc_paths, walk_errors, ignore_unknown = _discover_docs(root, git_ignore)

    raw_claims: list[dict[str, Any]] = []
    unreadable_docs: list[str] = []
    docs_scanned = 0
    for path in doc_paths:
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            unreadable_docs.append(f"{relative}: {exc.__class__.__name__}")
            continue
        docs_scanned += 1
        raw_claims.extend(_extract_claims(text, relative))

    with open_db(readonly=True) as conn:
        metrics = _count_metrics(conn, root)

    version_claims_present = any(claim["kind"] == "version" for claim in raw_claims)
    if version_claims_present:
        authority_version, authority_name, authority_reason = _version_authority(root)
    else:
        authority_version, authority_name, authority_reason = None, None, ""

    evaluated: list[dict[str, Any]] = []
    unavailable_items: list[str] = []
    for claim in raw_claims:
        if claim["kind"] == "path":
            finding = _evaluate_path_claim(claim, root, git_ignore)
        elif claim["kind"] == "count":
            finding = _evaluate_count_claim(claim, metrics)
        else:
            finding = _evaluate_version_claim(claim, authority_version, authority_name, authority_reason)
        if finding.get("_authority_unavailable"):
            unavailable_items.append(
                f"{finding['doc']}:{finding['line']} {finding['kind']} {finding['claim_text']}: {finding['reason']}"
            )
        evaluated.append(finding)

    evaluated.sort(key=lambda finding: (finding["doc"], finding["line"], finding["kind"], finding["claim_text"]))
    findings = [_public_finding(finding) for finding in evaluated]
    verified = sum(finding["status"] == "verified" for finding in findings)
    drifted = sum(finding["status"] == "drifted" for finding in findings)
    unverifiable = sum(finding["status"] == "unverifiable" for finding in findings)
    claims_total = len(findings)

    scan_warnings = [*walk_errors]
    scan_warnings.extend(f"git check-ignore unavailable for doc {doc}" for doc in ignore_unknown)
    partial_success = bool(unreadable_docs or scan_warnings or unavailable_items)
    gate_refused = gate_enabled and docs_scanned == 0
    gate_passed = not gate_refused and (not gate_enabled or drifted <= allowed_drift)
    verdict = _verdict(docs_scanned, claims_total, verified, drifted, unverifiable, gate_enabled)
    summary: dict[str, Any] = {
        "verdict": verdict,
        "docs_scanned": docs_scanned,
        "claims_total": claims_total,
        "verified": verified,
        "drifted": drifted,
        "unverifiable": unverifiable,
        "partial_success": partial_success,
        "gate_enabled": gate_enabled,
        "gate_passed": gate_passed,
        "allowed_drift": allowed_drift if gate_enabled else None,
    }
    if gate_refused:
        summary["state"] = "no_docs_scanned"
    elif claims_total == 0:
        summary["state"] = "zero_claims_extracted"
    if unreadable_docs:
        summary["unreadable_docs"] = unreadable_docs
    if unavailable_items:
        summary["unavailable_authorities"] = unavailable_items
    if scan_warnings:
        summary["scan_warnings"] = scan_warnings

    facts = [
        f"{docs_scanned} Markdown docs scanned",
        f"{verified} documentation claims verified",
        f"{drifted} documentation claims flagged",
        f"{unverifiable} documentation claims skipped",
    ]
    agent_contract = {
        "facts": facts,
        "next_commands": ["roam doc-drift --ci", "roam --json doc-drift"],
    }

    def _exit_after_output() -> None:
        if gate_enabled and not gate_passed:
            from roam.exit_codes import EXIT_GATE_FAILURE

            ctx.exit(EXIT_GATE_FAILURE)

    if sarif_mode:
        from roam.output.sarif import doc_drift_to_sarif, with_sarif_disclosures, write_sarif

        sarif = doc_drift_to_sarif(findings)
        disclosures = [*unreadable_docs, *scan_warnings, *unavailable_items]
        if gate_refused:
            disclosures.append("doc_drift_gate_refused:no_markdown_docs_scanned")
        if disclosures:
            sarif = with_sarif_disclosures(sarif, disclosures)
        click.echo(write_sarif(sarif))
        _exit_after_output()
        return

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "doc-drift",
                    summary=summary,
                    budget=token_budget,
                    findings=findings,
                    unreadable_docs=unreadable_docs,
                    unavailable_authorities=unavailable_items,
                    scan_warnings=scan_warnings,
                    agent_contract=agent_contract,
                )
            )
        )
        _exit_after_output()
        return

    click.echo(f"VERDICT: {verdict}")
    rows = [
        [
            finding["status"],
            finding["kind"],
            f"{finding['doc']}:{finding['line']}",
            finding["claim_text"],
            "—" if finding.get("actual") is None else str(finding["actual"]),
        ]
        for finding in findings
    ]
    if rows:
        table_budget = max(1, token_budget // 20) if token_budget else 100
        click.echo()
        click.echo(format_table(["Status", "Kind", "Location", "Claim", "Actual"], rows, budget=table_budget))
    if partial_success:
        click.echo(
            f"\nPARTIAL: {len(unreadable_docs)} unreadable docs, "
            f"{len(unavailable_items)} unavailable claim authorities, {len(scan_warnings)} scan warnings"
        )
    _exit_after_output()
