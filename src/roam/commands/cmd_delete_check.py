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

import os
import re
import shutil
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
    # W107/W120 composition: the global `roam --ci` lever also turns the
    # delete-check gate on, even when the user didn't pass the local
    # `--ci`. Per LAW 11, an explicit local `--ci` still wins (the OR
    # below promotes ci=True; there is no way for the user to express
    # "explicit off" from the local CLI today, so global --ci silently
    # enables local gating — symmetric with over-fetch / pr-bundle).
    if not ci and ctx.obj and ctx.obj.get("ci_mode"):
        ci = True

    ensure_index()
    root = find_project_root()

    # W607-J: Pattern-2 consumer-layer wiring — thread a warnings_out bucket
    # through the SUBPROCESS-shaped axes (engine fan-out + git diff-source).
    # cmd_delete_check is the fourth subprocess-axis consumer (paired with
    # cmd_grep W607-G + cmd_history_grep W607-H + cmd_refs_text W607-I —
    # completes the grep_helpers consumer quartet). Silent-fallback locations:
    #   1. ``detect_engine`` silently returns ``"fallback"`` when
    #      ROAM_GREP_ENGINE pins an absent binary → user pin not honored.
    #   2. ``run_search`` / ``_run_and_parse`` silently swallow
    #      FileNotFoundError + TimeoutExpired → return [] (looks like a
    #      no-match) while the subprocess never ran.
    #   3. ``indexed_file_scan`` silently OSError-skips unreadable files.
    #   4. ``_git_diff`` (the diff-source subprocess for --source
    #      working/staged/pr/head) returns ``(_, error_kind)`` on git
    #      missing / timeout / non-zero return. Already surfaces via
    #      ``git_error`` field; W607-J adds ``warnings_out`` mirror so a
    #      consumer scanning the bucket can detect the degrade lineage.
    #   5. Reachability degrade via ``build_reachable_set`` returning None.
    #      Already surfaces via Pattern-1D state/resolution disclosure;
    #      W607-J adds ``warnings_out`` mirror.
    # Marker family is ``delete_check_*`` (NOT ``grep_*`` / ``history_*`` /
    # ``refs_text_*`` / ``search_*`` / ``complete_*`` / ``semantic_*``) —
    # cmd_delete_check is the diff-gating-with-CI-exit-5 axis, distinct
    # shape from the sibling subprocess consumers. Empty bucket →
    # byte-identical envelope (hash-stable). Non-empty bucket →
    # summary.warnings_out + summary.partial_success=True + top-level
    # mirror.
    #
    # Complementary (NOT a replacement) to the W805-Z strict-xfail
    # Pattern-2 disclosure pins on the empty-corpus zero-survivors silent
    # SAFE path. W805-Z pins the empty-corpus state-disclosure axis
    # (state="empty_corpus" / partial_success=True / non-SAFE verdict /
    # exit 5 under --ci). W607-J adds the subprocess-degrade disclosure
    # axis. The W805-Z xfail-strict tests MUST remain xfailed after
    # W607-J lands.
    warnings_out: list[str] = []

    diff, git_err = _git_diff(root, source, base_ref, commit_range)
    if git_err is not None:
        # CP45/CP46 fail-loud: a CI gate that cannot read its diff MUST NOT
        # report SAFE. Surface the unavailability and (when --ci is set) exit
        # with the gate-failure code so the job fails rather than silently
        # passing on a host with no git installed.
        #
        # W607-J: also disclose the diff-source subprocess degrade via
        # ``warnings_out`` so an agent scanning the bucket can detect the
        # silent-degrade lineage independently of the existing
        # ``git_error`` field. Pattern-2 disclosure axis — the underlying
        # --source flag was honored, just the git subprocess that
        # implements it failed.
        warnings_out.append(f"delete_check_git_diff_failed:{git_err}:source={source!r} cannot read diff")
        verdict = f"diff unavailable: {git_err} — cannot gate"
        if sarif_mode:
            from roam.output.sarif import delete_check_to_sarif, write_sarif

            click.echo(write_sarif(delete_check_to_sarif({"command": "delete-check", "deletions": []})))
        elif json_mode:
            _summary: dict = {
                "verdict": verdict,
                "deletions": 0,
                "partial_success": True,
                "git_error": git_err,
            }
            _extra: dict = {}
            if warnings_out:
                _summary["warnings_out"] = list(warnings_out)
                _extra["warnings_out"] = list(warnings_out)
            click.echo(
                to_json(
                    json_envelope(
                        "delete-check",
                        budget=token_budget,
                        summary=_summary,
                        deletions=[],
                        **_extra,
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
    # W607-J: engine-pin honoring check (outer-guard). If the user pinned
    # ROAM_GREP_ENGINE to a specific binary AND the binary is not on PATH,
    # ``detect_engine`` silently returns ``"fallback"``. That's an unhonored
    # pin — disclose it via the ``delete_check_engine_pin_missing:`` marker
    # family. Mirrors the equivalent disclosure in cmd_grep (W607-G) /
    # cmd_history_grep (W607-H) / cmd_refs_text (W607-I).
    _engine_pin = os.environ.get("ROAM_GREP_ENGINE", "auto").strip().lower()
    engine = detect_engine()
    if _engine_pin in {"ripgrep", "rg"} and engine != "ripgrep":
        warnings_out.append(
            "delete_check_engine_pin_missing:ripgrep:binary 'rg' not on PATH (shutil.which returned None)"
        )
    elif _engine_pin in {"git", "git-grep"} and engine != "git":
        warnings_out.append("delete_check_engine_pin_missing:git:binary 'git' not on PATH (shutil.which returned None)")

    pattern_strings = sorted({t["name"] for t in targets})
    # W607-J: outer-guard around run_search. ``_run_and_parse`` silently
    # swallows FileNotFoundError + TimeoutExpired (returns []); other
    # exceptions (PermissionError on Windows when binary path is masked,
    # arbitrary OSError on weird filesystems) propagate. The outer-guard
    # catches THOSE and threads the marker.
    try:
        matches = run_search(
            patterns=pattern_strings,
            root=root,
            fixed_string=True,
            engine=engine,
        )
    except Exception as exc:  # noqa: BLE001 — W607-J outer-guard
        if engine == "ripgrep":
            warnings_out.append(f"delete_check_ripgrep_failed:{type(exc).__name__}:{exc}")
        elif engine == "git":
            warnings_out.append(f"delete_check_git_grep_failed:{type(exc).__name__}:{exc}")
        else:
            warnings_out.append(f"delete_check_engine_failed:{type(exc).__name__}:{exc}")
        matches = []

    if not matches and engine == "fallback":
        # W607-J: disclose the auto-fan-out fallthrough so the agent can
        # distinguish "no engines on PATH (fell through to indexed scan)"
        # from "engines present, just no matches". Same silent-fallback
        # shape as cmd_grep / cmd_refs_text.
        _rg_present = bool(shutil.which("rg"))
        _git_present = bool(shutil.which("git"))
        if not _rg_present and not _git_present:
            warnings_out.append("delete_check_engine_fanout_fallback:auto:neither 'rg' nor 'git' on PATH")
        compiled = [re.compile(re.escape(s)) for s in pattern_strings]
        try:
            with open_db(readonly=True) as conn_tmp:
                matches = indexed_file_scan(compiled, conn_tmp, root, [])
        except Exception as exc:  # noqa: BLE001 — W607-J outer-guard
            warnings_out.append(f"delete_check_indexed_scan_failed:{type(exc).__name__}:{exc}")
            matches = []

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
            # Pattern 1B/1D: degraded resolution — anchor symbol not in
            # index. Emit a structured envelope so MCP wrappers see
            # actionable state instead of a raw COMMAND_FAILED.
            #
            # W607-J: also disclose the reachability degrade via
            # ``warnings_out`` so an agent scanning the bucket can detect
            # the silent-degrade lineage independently of the existing
            # ``state``/``resolution`` Pattern-1D disclosure.
            warnings_out.append(
                f"delete_check_reachability_degraded:unresolved_entry:entry symbol '{reachable_from}' not found in index"
            )
            msg = f"entry symbol '{reachable_from}' not found in index"
            if json_mode:
                _summary: dict = {
                    "verdict": msg,
                    "state": "unresolved_entry",
                    "partial_success": True,
                    "resolution": "unresolved",
                    "deletions": 0,
                }
                _extra: dict = {}
                if warnings_out:
                    _summary["warnings_out"] = list(warnings_out)
                    _extra["warnings_out"] = list(warnings_out)
                click.echo(
                    to_json(
                        json_envelope(
                            "delete-check",
                            budget=token_budget,
                            summary=_summary,
                            hint="Verify the symbol exists; try `roam search <name>` first.",
                            deletions=[],
                            **_extra,
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: {msg}")
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
        # W607-J: non-empty bucket → summary mirror + partial_success flip
        # + top-level mirror. Empty bucket → byte-identical envelope
        # (hash-stable). Preserves the W805-Z Pattern-2 silent SAFE
        # contract (state disclosure) as a separate axis that W607-J
        # does NOT graduate.
        extra: dict = {}
        if warnings_out:
            summary["warnings_out"] = list(warnings_out)
            summary["partial_success"] = True
            extra["warnings_out"] = list(warnings_out)
        click.echo(
            to_json(
                json_envelope(
                    "delete-check",
                    budget=token_budget,
                    summary=summary,
                    deletions=results,
                    **extra,
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
