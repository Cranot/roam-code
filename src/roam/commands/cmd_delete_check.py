"""roam delete-check — gate the working diff on surviving references.

Walks the deletions in the working diff (or staged / PR / commit-range
diff), extracts every named symbol and removed file path, and searches
the unchanged code for surviving references. Reports per-deletion
verdict:

  * SAFE          — no surviving reference
  * LIKELY-SAFE   — survivors only in tests / docs
  * BREAK-RISK    — survivors in reachable code

Exits non-zero when any BREAK-RISK is detected (CI gate behaviour).
Pairs with the PR Replay narrative — the same audit-grade signal,
delivered before merge.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.commands.grep_helpers import (
    build_interval_index,
    build_orphan_set,
    build_reachable_set,
    classify_surface,
    detect_engine,
    find_enclosing,
    indexed_file_scan,
    run_search,
)
from roam.commands.resolve import ensure_index
from roam.db.connection import find_project_root, open_db
from roam.evidence._vocabulary import REFERENCE_REMOVAL_VERDICTS
from roam.git_utils import worktree_git_env
from roam.output.formatter import json_envelope, loc, to_json

# Exit code 5 signals a CI gate failure (matches cmd_rules)
EXIT_GATE_FAILURE = 5


def _validate_verdict(verdict: str) -> str:
    """Assert ``verdict`` is in the W1156 closed-enum vocabulary.

    Display form is UPPERCASE-WITH-HYPHENS; canonical form is
    lowercase+underscore. Normalize before membership check and return
    the original display form so callers stay unchanged.
    """
    canonical = verdict.lower().replace("-", "_")
    assert canonical in REFERENCE_REMOVAL_VERDICTS, (
        f"verdict {verdict!r} (canonical {canonical!r}) not in REFERENCE_REMOVAL_VERDICTS - see W1156"
    )
    return verdict


# ---------------------------------------------------------------------------
# Diff parsing — extract deletion candidates
# ---------------------------------------------------------------------------


_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")
_PY_CLASS_RE = re.compile(r"^\s*class\s+(\w+)\s*[:\(]")
_PY_CONST_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=")
_JS_FN_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(")
_JS_CLASS_RE = re.compile(r"^\s*(?:export\s+)?class\s+(\w+)\b")
_JS_CONST_RE = re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=")
_GO_FN_RE = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(")
_GO_TYPE_RE = re.compile(r"^\s*type\s+(\w+)\s+(?:struct|interface)\b")
_TS_TYPE_RE = re.compile(r"^\s*(?:export\s+)?(?:type|interface)\s+(\w+)\b")


def _extract_symbol_names(line: str) -> list[str]:
    """Best-effort identifier extraction from a deleted source line."""
    out = []
    for rx in (
        _PY_DEF_RE,
        _PY_CLASS_RE,
        _PY_CONST_RE,
        _JS_FN_RE,
        _JS_CLASS_RE,
        _JS_CONST_RE,
        _GO_FN_RE,
        _GO_TYPE_RE,
        _TS_TYPE_RE,
    ):
        m = rx.match(line)
        if m:
            out.append(m.group(1))
    return out


# CP45/CP46 fail-loud sentinels. ``_git_diff`` previously collapsed
# git-missing, git-timeout, and git-returned-error into the same ``""``
# shape used for "the diff is empty" — a CI runner with no git installed
# would receive a silent SAFE verdict from the gate. ``_git_diff`` now
# returns ``(diff_text, error_kind)`` so the command can surface the
# unavailability instead of treating it as a clean tree.
_GIT_MISSING = "git_not_available"
_GIT_TIMEOUT = "git_timeout"
_GIT_ERROR = "git_error"


def _git_diff(root: Path, source: str, base_ref: str, commit_range: str | None) -> tuple[str, str | None]:
    cmd = ["git", "diff", "--unified=0"]
    if commit_range:
        cmd.append(commit_range)
    elif source == "staged":
        cmd.append("--cached")
    elif source == "pr":
        cmd.append(f"{base_ref}...HEAD")
    elif source == "head":
        cmd.append("HEAD")
    # else: working-tree default
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(root),
        )
    except FileNotFoundError:
        return "", _GIT_MISSING
    except subprocess.TimeoutExpired:
        return "", _GIT_TIMEOUT
    if result.returncode != 0:
        return "", _GIT_ERROR
    return result.stdout, None


def _parse_deletions(diff_text: str) -> tuple[list[str], list[tuple[str, int, str, str]]]:
    """Return ``(deleted_files, deleted_lines)``.

    ``deleted_files`` is the list of files marked as fully deleted by
    the diff (mode delete). ``deleted_lines`` is ``[(path, line_no,
    line_text, kind)]`` where kind ∈ {'symbol', 'line'}.

    Symbol kinds carry an extracted identifier as ``line_text``; plain
    'line' kinds carry the raw deleted text.
    """
    deleted_files: list[str] = []
    deleted_lines: list[tuple[str, int, str, str]] = []

    current_file: str | None = None
    current_old_line = 0
    pending_deletion = False

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            # Reset state
            current_file = None
            current_old_line = 0
            pending_deletion = False
            continue
        if raw.startswith("--- a/"):
            current_file = raw[6:].replace("\\", "/")
            continue
        if raw.startswith("deleted file mode"):
            pending_deletion = True
            continue
        if raw.startswith("@@"):
            m = re.search(r"-(\d+)(?:,\d+)?", raw)
            current_old_line = int(m.group(1)) if m else 0
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            if current_file is None:
                continue
            text = raw[1:]
            symbols = _extract_symbol_names(text)
            if symbols:
                for s in symbols:
                    deleted_lines.append((current_file, current_old_line, s, "symbol"))
            else:
                stripped = text.strip()
                if stripped and not stripped.startswith(("#", "//", "/*")):
                    deleted_lines.append((current_file, current_old_line, stripped, "line"))
            current_old_line += 1
        elif raw.startswith(" "):
            # Context line in unified=0 diff is rare but harmless
            current_old_line += 1
        elif raw.startswith("+") and not raw.startswith("+++"):
            pass

        # Track full-file deletions
        if pending_deletion and current_file:
            if current_file not in deleted_files:
                deleted_files.append(current_file)

    return deleted_files, deleted_lines


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@roam_capability(
    name="delete-check",
    category="refactoring",
    summary="Gate the working diff on surviving references to deleted symbols / files",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core", "refactor"),
    side_effect=False,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
@click.command("delete-check")
@click.option(
    "--source",
    type=click.Choice(["working", "staged", "pr", "head"]),
    default="working",
    help="Which diff to gate. 'pr' uses base_ref...HEAD; 'head' uses last commit.",
)
@click.option("--base-ref", default="main", help="Base branch for --source pr.")
@click.option("--commit-range", default=None, help="Arbitrary git range, e.g. HEAD~3..HEAD.")
@click.option("--reachable-from", "reachable_from", default=None, help="Anchor reachability classification at <entry>.")
@click.option("--ci", is_flag=True, help="Exit 5 on BREAK-RISK so CI fails the job.")
@click.option("-n", "count", default=20, help="Max deletions to report in detail.")
@click.option("--include-line-deletions/--symbols-only", default=False, help="Also gate on raw deleted lines (slow).")
@click.pass_context
def delete_check_cmd(ctx, source, base_ref, commit_range, reachable_from, ci, count, include_line_deletions):
    """Gate the working diff on surviving references to deleted symbols / files.

    Examples:

      \b
      roam delete-check                      # gate the working tree
      roam delete-check --source staged
      roam delete-check --source pr --base-ref main --ci
      roam delete-check --reachable-from main --ci
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    ensure_index()
    root = find_project_root()

    diff, git_err = _git_diff(root, source, base_ref, commit_range)
    if git_err is not None:
        # CP45/CP46 fail-loud: a CI gate that cannot read its diff MUST NOT
        # report SAFE. Surface the unavailability and (when --ci is set) exit
        # with the gate-failure code so the job fails rather than silently
        # passing on a host with no git installed.
        verdict = f"diff unavailable: {git_err} — cannot gate"
        if sarif_mode:
            from roam.output.sarif import delete_check_to_sarif, write_sarif

            click.echo(write_sarif(delete_check_to_sarif({"command": "delete-check", "deletions": []})))
        elif json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "delete-check",
                        budget=token_budget,
                        summary={
                            "verdict": verdict,
                            "deletions": 0,
                            "partial_success": True,
                            "git_error": git_err,
                        },
                        deletions=[],
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        if ci:
            raise SystemExit(EXIT_GATE_FAILURE)
        return
    if not diff.strip():
        if sarif_mode:
            # W1192: SARIF projection for CI / GitHub Code Scanning
            # integration. Empty-diff path emits a valid SARIF doc with
            # zero results (rules catalogue is always populated so
            # consumers can introspect the rule vocabulary even on a
            # clean run). Branches BEFORE json/text paths so those
            # legacy paths stay byte-identical to pre-W1192 output.
            from roam.output.sarif import delete_check_to_sarif, write_sarif

            click.echo(write_sarif(delete_check_to_sarif({"command": "delete-check", "deletions": []})))
            return
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "delete-check",
                        budget=token_budget,
                        summary={"verdict": "no deletions detected", "deletions": 0},
                        deletions=[],
                    )
                )
            )
        else:
            click.echo("VERDICT: no deletions detected — nothing to check.")
        return

    deleted_files, deleted_lines = _parse_deletions(diff)
    fully_deleted = set(deleted_files)

    # Build gate targets — symbols first (cheap, precise), files second.
    targets: list[dict] = []
    seen_symbols: set[tuple[str, str]] = set()
    for path, line, text, kind in deleted_lines:
        if kind == "symbol":
            key = (path, text)
            if key in seen_symbols:
                continue
            seen_symbols.add(key)
            targets.append({"kind": "symbol", "name": text, "from_file": path, "from_line": line})
        elif include_line_deletions:
            targets.append({"kind": "line", "name": text, "from_file": path, "from_line": line})

    for f in deleted_files:
        targets.append({"kind": "file", "name": f, "from_file": f, "from_line": 0})

    if not targets:
        if sarif_mode:
            # W1192: same empty-deletions SARIF envelope as the empty-diff
            # path above — preserves the "clean run" signal in SARIF
            # without inventing a rule for "nothing to gate".
            from roam.output.sarif import delete_check_to_sarif, write_sarif

            click.echo(write_sarif(delete_check_to_sarif({"command": "delete-check", "deletions": []})))
            return
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "delete-check",
                        budget=token_budget,
                        summary={"verdict": "no symbol or file deletions detected", "deletions": 0},
                        deletions=[],
                    )
                )
            )
        else:
            click.echo("VERDICT: diff contains only intra-symbol changes — no gate needed.")
        return

    # Search for surviving references
    engine = detect_engine()
    pattern_strings = sorted({t["name"] for t in targets})
    matches = run_search(
        patterns=pattern_strings,
        root=root,
        fixed_string=True,
        engine=engine,
    )
    if not matches and engine == "fallback":
        compiled = [re.compile(re.escape(s)) for s in pattern_strings]
        with open_db(readonly=True) as conn_tmp:
            matches = indexed_file_scan(compiled, conn_tmp, root, [])

    # Matches come from the post-edit working tree, so deleted lines are
    # already gone — no double-counting risk. Only filter out files the
    # diff fully removed (their content shouldn't contribute as a survivor).
    surviving = [m for m in matches if m["path"] not in fully_deleted]

    with open_db(readonly=True) as conn:
        match_paths = {m["path"] for m in surviving}
        interval_idx = build_interval_index(conn, match_paths)
        for m in surviving:
            sym = find_enclosing(interval_idx, m["path"], m["line"])
            m["_enclosing"] = sym
            m["enclosing_symbol"] = sym["qualified_name"] if sym else None
            m["enclosing_kind"] = sym["kind"] if sym else None

        # Reachability annotation
        reach_set = build_reachable_set(conn, reachable_from) if reachable_from else None
        if reachable_from and reach_set is None:
            click.echo(f"VERDICT: entry symbol '{reachable_from}' not found in index")
            raise SystemExit(1)
        orphans = build_orphan_set(conn) if reach_set is None else set()
        for m in surviving:
            sym = m["_enclosing"]
            if reach_set is not None:
                m["reachable"] = bool(sym and sym["id"] in reach_set)
            else:
                m["reachable"] = bool(sym and sym["id"] not in orphans)
            m["surface"] = classify_surface(m["path"])

        # Bucket per target
        per_target: dict[str, list[dict]] = {t["name"]: [] for t in targets}
        for m in surviving:
            for t in targets:
                if t["name"] in m["content"] or (t["kind"] == "file" and t["name"] in m["content"]):
                    per_target[t["name"]].append(m)

    # Verdict per target
    decorated = []
    any_break = False
    # Evidence-first ordering for the truncated [:5] survivors view (LAW 4 /
    # Pattern-1D): when the verdict is BREAK-RISK because of "N surviving
    # reachable code reference(s)", the first 5 rendered survivors must BE
    # those reachable code refs — otherwise an agent reading the truncated
    # JSON sees `reachable: false` everywhere and disbelieves the verdict.
    # Rank: reachable code (0) < unreachable code (1) < test (2) < other (3).
    def _evidence_rank(m: dict) -> tuple[int, str, int]:
        surface = m.get("surface", "other")
        reachable = bool(m.get("reachable"))
        if surface == "code" and reachable:
            band = 0
        elif surface == "code":
            band = 1
        elif surface == "test":
            band = 2
        else:
            band = 3
        return (band, m.get("path", ""), m.get("line", 0))

    for t in targets:
        items = per_target.get(t["name"], [])
        verdict, reason = _verdict(items)
        if verdict == "BREAK-RISK":
            any_break = True
        # Sort matches so the truncated [:5] view foregrounds the evidence
        # supporting the verdict. Stable across SARIF / JSON / text paths.
        items_sorted = sorted(items, key=_evidence_rank)
        decorated.append({**t, "verdict": verdict, "reason": reason, "matches": items_sorted})

    # Sort: BREAK-RISK first, then LIKELY-SAFE, then SAFE
    rank = {"BREAK-RISK": 0, "LIKELY-SAFE": 1, "SAFE": 2}
    decorated.sort(key=lambda d: (rank.get(d["verdict"], 3), d["name"]))

    breaks = sum(1 for d in decorated if d["verdict"] == "BREAK-RISK")
    likely = sum(1 for d in decorated if d["verdict"] == "LIKELY-SAFE")
    safe = sum(1 for d in decorated if d["verdict"] == "SAFE")
    overall = "BREAK-RISK" if breaks else ("LIKELY-SAFE" if likely else "SAFE")

    if sarif_mode:
        # W1192: SARIF projection for CI / GitHub Code Scanning integration.
        # The ``--ci`` exit-5 gate below stays identical to the JSON / text
        # paths so the CI behaviour is invariant across output formats. The
        # SARIF document follows the same per-deletion shape used by the
        # JSON path: PRIMARY anchor = ``from_file:from_line``; SECONDARY =
        # up to 10 survivors[] entries. The ``--json`` and text paths stay
        # byte-identical to pre-W1192 output (this branch short-circuits
        # before them; nothing above changed shape).
        from roam.output.sarif import delete_check_to_sarif, write_sarif

        deletions_for_sarif = []
        for d in decorated[:count]:
            deletions_for_sarif.append(
                {
                    "kind": d["kind"],
                    "name": d["name"],
                    "from_file": d["from_file"],
                    "from_line": d["from_line"],
                    "verdict": d["verdict"],
                    "reason": d["reason"],
                    "survivors": [
                        {
                            "path": m["path"],
                            "line": m["line"],
                            "enclosing_symbol": m.get("enclosing_symbol"),
                            "reachable": m.get("reachable"),
                            "surface": m.get("surface"),
                        }
                        for m in d["matches"][:5]
                    ],
                }
            )
        click.echo(write_sarif(delete_check_to_sarif({"command": "delete-check", "deletions": deletions_for_sarif})))
    elif json_mode:
        results = []
        for d in decorated[:count]:
            results.append(
                {
                    "kind": d["kind"],
                    "name": d["name"],
                    "from_file": d["from_file"],
                    "verdict": d["verdict"],
                    "reason": d["reason"],
                    "survivors": [
                        {
                            "path": m["path"],
                            "line": m["line"],
                            "content": m["content"],
                            "enclosing_symbol": m.get("enclosing_symbol"),
                            "reachable": m.get("reachable"),
                            "surface": m.get("surface"),
                        }
                        for m in d["matches"][:5]
                    ],
                }
            )
        summary = {
            "verdict": (f"{len(decorated)} deletion(s): {breaks} break-risk, {likely} likely-safe, {safe} safe"),
            "overall": overall,
            "break_risk": breaks,
            "likely_safe": likely,
            "safe": safe,
        }
        click.echo(
            to_json(
                json_envelope(
                    "delete-check",
                    budget=token_budget,
                    summary=summary,
                    deletions=results,
                )
            )
        )
    else:
        click.echo(f"VERDICT: {len(decorated)} deletion(s) — {breaks} BREAK-RISK / {likely} LIKELY-SAFE / {safe} SAFE")
        click.echo()
        for d in decorated[:count]:
            click.echo(f"  {d['verdict']:11s} {d['kind']:6s} {d['name']}  ({d['reason']})")
            for m in d["matches"][:3]:
                tag = "[reachable]" if m.get("reachable") else "[unreachable]"
                sym = m.get("enclosing_symbol")
                click.echo(f"    - {loc(m['path'], m['line'])}{f' in {sym}' if sym else ''} {tag}")
            if len(d["matches"]) > 3:
                click.echo(f"    ... +{len(d['matches']) - 3} more survivors")
        if len(decorated) > count:
            click.echo(f"\n(+{len(decorated) - count} more deletions)")

    if ci and any_break:
        raise SystemExit(EXIT_GATE_FAILURE)


def _verdict(survivors: list[dict]) -> tuple[str, str]:
    if not survivors:
        return _validate_verdict("SAFE"), "no surviving references"
    code = [m for m in survivors if m["surface"] in ("code",)]
    reachable = [m for m in code if m.get("reachable", True)]
    test = [m for m in survivors if m["surface"] == "test"]
    docs = [m for m in survivors if m["surface"] == "docs"]
    if reachable:
        return _validate_verdict("BREAK-RISK"), f"{len(reachable)} surviving reachable code reference(s)"
    if code:
        return _validate_verdict("LIKELY-SAFE"), f"{len(code)} reference(s) only in unreachable code"
    if test or docs:
        return _validate_verdict("LIKELY-SAFE"), f"{len(test)} test / {len(docs)} doc reference(s)"
    return _validate_verdict("LIKELY-SAFE"), f"{len(survivors)} reference(s) in non-code surfaces"
