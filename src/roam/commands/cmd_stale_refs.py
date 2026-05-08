"""Detect dangling references in markdown / HTML / code to files that no longer exist.

Pure filesystem scan — does not need ``roam index``. Catches what
symbol-graph commands (``uses``, ``impact``, ``refs``) miss: prose
mentions of file paths, markdown links, HTML href/src attributes, and
backtick file references whose target was renamed or deleted.

Companion to :mod:`roam.commands.cmd_doc_staleness` (stale docstring
content) and :mod:`roam.commands.cmd_docs_coverage` (missing public-symbol
docs). Where those two audit *what* the docs say, ``stale-refs`` audits
*where* the docs point.

Beyond plain dangling-path detection, this command layers on:

* **Markdown anchor validation** — a reference like
  ``[deploy](docs/cd.md#cloudflare-pages)`` is also flagged when the
  file exists but the anchor is missing. See :mod:`stale_refs_anchors`.
* **Confidence-tagged rename hints** — git-history renames are HIGH
  confidence; basename matches are HIGH/MEDIUM/LOW depending on
  uniqueness; symbol-graph similarity fills in when the index exists.
  See :mod:`stale_refs_hints`.
* **Branch-diff mode** — ``--diff`` filters findings to only those new
  in the current branch (introduced refs OR newly-deleted targets), so
  ``--gate`` becomes practical on repos with historical CHANGELOG noise.
* **Auto-fix** — ``--fix preview`` shows the unified diff,
  ``--fix apply`` rewrites in place, but only for HIGH-confidence hints.
* **Importance sort** — ``--sort-by priority`` (default) outweighs
  README/CHANGELOG/docs over templates/fixtures/samples and recent
  edits over stale ones, so the top-N findings are the actionable ones.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import click

from roam.commands.stale_refs_anchors import AnchorCache
from roam.commands.stale_refs_hints import HintContext, best_hint
from roam.db.connection import find_project_root
from roam.index.discovery import discover_files
from roam.output.formatter import json_envelope, to_json

# ---------------------------------------------------------------------------
# Reference patterns
# ---------------------------------------------------------------------------

# Markdown inline link / image:  [text](url)  or  ![alt](url)
# A leading ``!`` is allowed (image); we don't capture it.
_MD_INLINE_RE = re.compile(r"!?\[(?P<text>[^\]\n]*)\]\((?P<url>[^)\n]+)\)")

# Reference-style link definition:  [label]: url  (with optional "title")
_MD_REFERENCE_RE = re.compile(r"^[ \t]{0,3}\[(?P<label>[^\]\n]+)\]:[ \t]+(?P<url>\S+)")

# HTML href= / src= attribute (single or double quoted).
_HTML_ATTR_RE = re.compile(r"(?P<attr>href|src)=(?:\"(?P<v1>[^\"]*)\"|'(?P<v2>[^']*)')")

# Backtick-wrapped doc-shaped path (e.g., `internal backlog`).
# Limited to multi-segment-or-extension paths so prose noise doesn't drown
# real findings.
_BACKTICK_PATH_RE = re.compile(
    r"`(?P<path>[^`\s]+\.(?:md|markdown|txt|rst|yml|yaml|json|html?|toml|ini|cfg|"
    r"sh|ps1|py|js|jsx|ts|tsx|go|rs|java|kt|kts|scala|rb|cs|php|swift|"
    r"cpp|cxx|cc|c|h|hpp|sql|proto|graphql))`"
)

# Schemes / shapes that are NOT a local path reference.
_SKIP_SCHEME_RE = re.compile(
    r"^(?:https?:|mailto:|tel:|ftp:|sftp:|ssh:|file:|data:|javascript:|//|git@)",
    re.IGNORECASE,
)

# Cheap content sniff — files with none of these characters cannot contain
# any of the four reference shapes we look for. Skipping the regex pass on
# such files (lock-files, manifests, generated YAML) cuts wall-clock by
# ~30% on the roam-code repo.
_REF_TRIGGER_CHARS = ("[", "<", "`")

# Extensions we *scan* for references. Anything else is filtered upstream
# by ``discover_files``. Narrowed to plausible text containers.
_SCANNABLE_EXTS = frozenset(
    {
        "",  # README, LICENSE, CHANGELOG without extension
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".html",
        ".htm",
        ".xml",
        ".svg",  # scanned for href/src, even though it's binary-ish
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".py",
        ".pyx",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".rb",
        ".cs",
        ".php",
        ".swift",
        ".cpp",
        ".cxx",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".sql",
        ".proto",
        ".graphql",
        ".vue",
        ".svelte",
    }
)

# Folders that static-site frameworks (Next.js, Astro, Vite, plain HTML)
# commonly serve from the deployment root. ``/favicon.svg`` ↔
# ``public/favicon.svg`` on disk.
_PUBLIC_FOLDER_CANDIDATES = ("public", "static", "assets", "docs/site", "site")

# Path prefixes that indicate runtime-generated / deploy-only / dependency
# directories. References pointing into these are intentionally absent
# from VCS — flagging them is noise.
_RUNTIME_PATH_PREFIXES = (
    ".roam/",
    ".git/",
    ".github/",  # workflows reference each other / external actions
    "node_modules/",
    ".next/",
    ".nuxt/",
    "dist/",
    "build/",
    "target/",
    ".venv/",
    "venv/",
    ".cache/",
    "__pycache__/",
)

# Reference kinds that only make sense inside prose / markup files.
# In source code, ``[text](url)`` collisions with regex character classes
# (``[^'"]+`` mis-parsed as a markdown link) drown out real findings, so
# we restrict markdown-shaped detection to prose-shaped files.
_PROSE_EXTS = frozenset(
    {
        ".md",
        ".markdown",
        ".rst",
        ".txt",
        ".html",
        ".htm",
        ".xml",
        ".svg",
        "",  # README / LICENSE / CHANGELOG without extension
    }
)


# ---------------------------------------------------------------------------
# URL skip rules (shared by both resolvers)
# ---------------------------------------------------------------------------


def _strip_url_decorations(url: str) -> str:
    """Drop wrapping ``<...>``, trailing punctuation, and any ``#fragment`` / ``?query``."""
    url = url.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    url = url.rstrip(",.;:")
    # Fragments may themselves contain '?', so split on '#' first.
    url = url.split("#", 1)[0]
    url = url.split("?", 1)[0]
    return url


def _extract_fragment(url: str) -> str:
    """Return the bare ``#anchor`` slug, or ``""`` when absent.

    Mirrors :func:`_strip_url_decorations` — same wrapping/whitespace
    rules — so the path resolver and the anchor validator see consistent
    raw inputs from one call.
    """
    url = url.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    url = url.rstrip(",.;:")
    if "#" not in url:
        return ""
    fragment = url.split("#", 1)[1]
    fragment = fragment.split("?", 1)[0]
    return fragment.strip()


def _is_runtime_path(rel_path: str) -> bool:
    """True when *rel_path* falls under a runtime-generated directory."""
    norm = rel_path.replace("\\", "/")
    return any(norm.startswith(prefix) for prefix in _RUNTIME_PATH_PREFIXES)


def _should_skip_url(cleaned: str) -> bool:
    """Return True when *cleaned* is not a checkable file reference.

    Single source of truth for the "this URL isn't a real file path" rules:
    empty / scheme / anchor-only / placeholder-glob / runtime-prefix.
    """
    if not cleaned:
        return True
    if _SKIP_SCHEME_RE.match(cleaned):
        return True
    if cleaned.startswith("#"):
        return True
    # Placeholders (``<project_root>/foo``) and globs (``docs/*.html``,
    # ``prompts/{task}_{mode}.txt``) are documentation patterns, not
    # concrete paths.
    if any(ch in cleaned for ch in "<*{"):
        return True
    if _is_runtime_path(cleaned.lstrip("/")):
        return True
    return False


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _safe_relative_to_root(path: Path, project_root: Path) -> Path | None:
    """Return *path* if it lives under *project_root*, else None."""
    try:
        path.relative_to(project_root)
    except ValueError:
        return None
    return path


def _resolve_target(
    url: str,
    source_file_rel: str,
    project_root: Path,
    *,
    check_absolute_routes: bool = False,
) -> Path | None:
    """Resolve a markdown link / HTML attr URL to a path on disk.

    Resolution rules:

    * URLs starting with ``/`` are absolute-from-deploy-root. By default
      we only check them when they carry a file extension — extensionless
      ``href="/setup"`` style routes are framework / static-site routes
      (Next.js, Astro, plain SPAs) that don't exist as files. Pass
      ``check_absolute_routes=True`` to force strict file-system lookup.
    * Absolute paths with extensions try project-root first, then common
      public-folder mappings (``public/``, ``static/``, ``assets/``), then
      walk the source file's ancestor chain — static-site frameworks
      typically build from a sub-tree, so the deploy-root maps to the
      directory the HTML lives in.
    * Everything else resolves relative to the source file's directory.

    Returns ``None`` when the URL is unchecked (scheme / anchor / runtime
    prefix / placeholder) or escapes the project root.
    """
    cleaned = _strip_url_decorations(url)
    if _should_skip_url(cleaned):
        return None

    if cleaned.startswith("/"):
        has_extension = bool(os.path.splitext(cleaned)[1])
        if not has_extension and not check_absolute_routes:
            return None
        rel = cleaned.lstrip("/")
        candidate = (project_root / rel).resolve()
        if candidate.exists():
            return candidate
        for folder in _PUBLIC_FOLDER_CANDIDATES:
            alt = (project_root / folder / rel).resolve()
            if alt.exists():
                return alt
        # Walk source file's ancestor chain — deploy-root may equal the
        # directory the source HTML lives in.
        cur = (project_root / source_file_rel).parent
        if _safe_relative_to_root(cur, project_root) is None:
            cur = project_root
        while True:
            alt = (cur / rel).resolve()
            if alt.exists():
                return alt
            if cur == project_root:
                break
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
        return _safe_relative_to_root(candidate, project_root)

    candidate = ((project_root / source_file_rel).parent / cleaned).resolve()
    return _safe_relative_to_root(candidate, project_root)


def _resolve_backtick_target(
    url: str,
    source_file_rel: str,
    project_root: Path,
    *,
    basename_idx: dict[str, list[str]] | None = None,
    prose_mode: bool = True,
) -> Path | None:
    """Resolve a backtick-wrapped path with both source-relative AND root anchors.

    Returns ``None`` when:

    * the URL targets a runtime-generated path,
    * either anchor (source-relative or project-root) resolves on disk,
    * the URL is a bare filename and any file in the repo has that
      basename — bare references like `` `cli.py` `` are generic mentions,
      not specific path claims,
    * the URL is a bare filename inside a *source-code* file (``.py``,
      ``.ts``, …): there it's almost always a placeholder or example
      identifier (``auth.py``, ``cmd_FOO.py``), not a concrete claim.
    """
    cleaned = _strip_url_decorations(url)
    if _should_skip_url(cleaned):
        return None

    is_bare = "/" not in cleaned and "\\" not in cleaned

    # Bare dotfile basenames (``.eslintrc``, ``.roam-gates.yml``) are
    # documentation about user-creatable optional config files — skip.
    if is_bare and cleaned.startswith("."):
        return None

    # Bare basename anywhere in the repo? Treat as live regardless of mode.
    if is_bare and basename_idx is not None and cleaned in basename_idx:
        return None

    # In source-code files, bare basenames that DON'T match any existing
    # file are almost always placeholders ("see auth.py", "cmd_FOO.py").
    # Skip them — false-positive risk dominates real findings.
    if is_bare and not prose_mode:
        return None

    # Source-relative — narrative "see ./foo.md" style.
    src_rel: Path | None = ((project_root / source_file_rel).parent / cleaned).resolve()
    src_rel = _safe_relative_to_root(src_rel, project_root)
    if src_rel is not None and src_rel.exists():
        return src_rel

    # Project-root anchor — "the project's `.roam/rules.yml`" style.
    root_anchor = _safe_relative_to_root((project_root / cleaned).resolve(), project_root)
    if root_anchor is None:
        return None
    if root_anchor.exists():
        return root_anchor

    # Neither resolved — return whichever anchor we have so the report
    # names a sensible canonical-missing target.
    return src_rel if src_rel is not None else root_anchor


# ---------------------------------------------------------------------------
# Per-file scanner
# ---------------------------------------------------------------------------


def _has_ref_triggers(content: str) -> bool:
    """Cheap substring sniff — files without any trigger char have no refs."""
    return any(ch in content for ch in _REF_TRIGGER_CHARS)


def _extract_refs(content: str, *, prose_mode: bool) -> list[tuple[int, str, str]]:
    """Extract ``(line_number, kind, url)`` triples from one file's text.

    Line numbers are 1-based. ``kind`` is one of ``md_inline``,
    ``md_reference``, ``html_attr``, ``backtick``.

    When *prose_mode* is ``False`` (source-code file), only backtick paths
    are extracted — markdown link syntax collides with regex character
    classes (``[^'"]+`` etc.) in code and produces a flood of false
    positives.
    """
    refs: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if prose_mode:
            for m in _MD_INLINE_RE.finditer(line):
                refs.append((lineno, "md_inline", m.group("url")))
            m_ref = _MD_REFERENCE_RE.match(line)
            if m_ref:
                refs.append((lineno, "md_reference", m_ref.group("url")))
            for m in _HTML_ATTR_RE.finditer(line):
                url = m.group("v1") if m.group("v1") is not None else m.group("v2")
                if url:
                    refs.append((lineno, "html_attr", url))
        for m in _BACKTICK_PATH_RE.finditer(line):
            refs.append((lineno, "backtick", m.group("path")))
    return refs


def _read_text_safe(path: Path, max_bytes: int = 1_000_000) -> str | None:
    """Read a file as UTF-8 with replacement; return None on read error or oversize."""
    try:
        if path.stat().st_size > max_bytes:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def _matches_any_glob(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """Return True when *rel_path* matches any glob in *patterns*.

    Patterns use :mod:`fnmatch` semantics (``*`` is single-segment) and are
    matched against the full path; users typically pass ``CHANGELOG.md`` or
    ``docs/legacy/*.md``. Both *rel_path* and the patterns are normalised
    to forward slashes so Windows users can pass either ``docs/old/*`` or
    ``docs\\old\\*`` interchangeably.
    """
    if not patterns:
        return False
    norm = rel_path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(norm, p.replace("\\", "/")) for p in patterns)


def _scan_project(
    project_root: Path,
    *,
    include_excluded: bool,
    check_absolute_routes: bool = False,
    ignore_source: tuple[str, ...] = (),
    ignore_target: tuple[str, ...] = (),
    check_anchors: bool = True,
) -> tuple[dict[str, list[dict]], int, int, dict[str, list[str]]]:
    """Walk the repo and collect every reference.

    Returns ``(stale_by_target, files_scanned, refs_seen, basename_idx)``.

    * ``stale_by_target`` maps the resolved missing target (relative path
      string) to a list of source records
      ``{file, line, kind, raw, anchor?}``. The optional ``anchor`` key
      is populated when the finding is anchor-only (file exists but
      ``#fragment`` doesn't); the synthetic target string in that case
      is ``"<file>#<anchor>"`` so multi-anchor failures inside the same
      file still group cleanly.
    * ``basename_idx`` is the basename → [paths] map, reusable by the
      caller for rename hints without re-running discovery.
    """
    all_files = discover_files(project_root, include_excluded=include_excluded)

    tracked_set = set(all_files)
    dir_set: set[str] = set()
    for p in all_files:
        parent = os.path.dirname(p)
        while parent:
            dir_set.add(parent)
            parent = os.path.dirname(parent)

    basename_idx: dict[str, list[str]] = defaultdict(list)
    for p in all_files:
        basename_idx[os.path.basename(p)].append(p)

    anchor_cache = AnchorCache(project_root)

    stale_by_target: dict[str, list[dict]] = defaultdict(list)
    files_scanned = 0
    refs_seen = 0

    for rel in all_files:
        ext = os.path.splitext(rel)[1].lower()
        if ext not in _SCANNABLE_EXTS:
            continue
        if _matches_any_glob(rel, ignore_source):
            continue
        full = project_root / rel
        content = _read_text_safe(full)
        if content is None:
            continue
        files_scanned += 1
        if not _has_ref_triggers(content):
            continue
        prose_mode = ext in _PROSE_EXTS

        for lineno, kind, raw_url in _extract_refs(content, prose_mode=prose_mode):
            refs_seen += 1
            fragment = _extract_fragment(raw_url) if kind != "backtick" else ""

            if kind == "backtick":
                target = _resolve_backtick_target(
                    raw_url,
                    rel,
                    project_root,
                    basename_idx=basename_idx,
                    prose_mode=prose_mode,
                )
            else:
                target = _resolve_target(
                    raw_url,
                    rel,
                    project_root,
                    check_absolute_routes=check_absolute_routes,
                )
            if target is None:
                continue
            try:
                rel_target = target.relative_to(project_root).as_posix()
            except ValueError:
                continue
            # Skip refs into runtime / dependency dirs — intentionally absent.
            if _is_runtime_path(rel_target):
                continue

            target_exists = rel_target in tracked_set or rel_target in dir_set or target.exists()

            if target_exists:
                # File is live; check the anchor when one was specified.
                if check_anchors and fragment and AnchorCache.is_anchor_validatable(rel_target):
                    anchors = anchor_cache.anchors_for(rel_target)
                    if anchors is not None and fragment not in anchors:
                        synthetic = f"{rel_target}#{fragment}"
                        if _matches_any_glob(synthetic, ignore_target) or _matches_any_glob(rel_target, ignore_target):
                            continue
                        stale_by_target[synthetic].append(
                            {
                                "file": rel,
                                "line": lineno,
                                "kind": "anchor",
                                "raw": raw_url,
                                "anchor": fragment,
                                "anchor_target_file": rel_target,
                            }
                        )
                continue

            if _matches_any_glob(rel_target, ignore_target):
                continue
            entry: dict = {
                "file": rel,
                "line": lineno,
                "kind": kind,
                "raw": raw_url,
            }
            if fragment:
                entry["anchor"] = fragment
            stale_by_target[rel_target].append(entry)

    return stale_by_target, files_scanned, refs_seen, basename_idx


def _hint_for_target(
    rel_target: str,
    sources: list[dict],
    hint_ctx: HintContext,
) -> dict | None:
    """Return a rich hint dict for a missing target, or ``None``.

    Anchor-only findings (target file exists, ``#fragment`` missing) get
    no rename hint — there's nothing to rename to. Regular dangling-path
    findings consult the provider chain (``git-history`` → ``symbol-graph``
    → ``basename``) and return the best with confidence + reason.
    """
    if sources and sources[0].get("kind") == "anchor":
        return None
    # Strip a synthetic ``#anchor`` segment if it leaked in (defensive).
    bare_target = rel_target.split("#", 1)[0]
    h = best_hint(bare_target, hint_ctx)
    if h is None:
        return None
    return {
        "target": h.target,
        "confidence": h.confidence,
        "reason": h.reason,
        "source": h.source,
    }


# ---------------------------------------------------------------------------
# Branch-diff filter
# ---------------------------------------------------------------------------


def _git_merge_base(project_root: Path, base_ref: str) -> str | None:
    """Resolve a merge-base SHA for ``HEAD`` against *base_ref*.

    Returns ``None`` when git isn't available, the ref doesn't exist, or
    the repo doesn't have a discoverable common ancestor.
    """
    try:
        result = subprocess.run(
            ["git", "merge-base", "HEAD", base_ref],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _git_changed_files(project_root: Path, base_sha: str) -> tuple[set[str], set[str]] | None:
    """Return ``(changed_in_branch, deleted_in_branch)`` since *base_sha*.

    Both sets contain repo-relative POSIX paths. ``changed_in_branch``
    includes added + modified + renamed-new-name files. ``deleted_in_branch``
    includes deleted + renamed-old-name files (the latter so a rename
    surfaces broken refs to the old path on the merge base).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", base_sha, "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    changed: set[str] = set()
    deleted: set[str] = set()
    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("A") or status.startswith("M"):
            if len(parts) >= 2:
                changed.add(parts[1].replace("\\", "/"))
        elif status.startswith("D"):
            if len(parts) >= 2:
                deleted.add(parts[1].replace("\\", "/"))
        elif status.startswith("R") and len(parts) >= 3:
            # Renamed: old-name acts like deletion, new-name like change.
            deleted.add(parts[1].replace("\\", "/"))
            changed.add(parts[2].replace("\\", "/"))
    return changed, deleted


def _resolve_diff_base(project_root: Path, requested: str) -> str | None:
    """Pick a sensible default base ref when ``--diff`` is bare.

    Order: explicit ``requested`` value if non-empty → ``origin/main`` →
    ``main`` → ``master`` → ``HEAD~1``. Returns the merge-base SHA, or
    ``None`` when nothing resolves (caller should report and skip diff
    filtering).
    """
    candidates = [requested] if requested else ["origin/main", "main", "master", "HEAD~1"]
    for ref in candidates:
        sha = _git_merge_base(project_root, ref)
        if sha:
            return sha
    return None


def _filter_diff_targets(
    stale_by_target: dict[str, list[dict]],
    changed: set[str],
    deleted: set[str],
) -> dict[str, list[dict]]:
    """Keep only findings new in the branch.

    A finding is "new" when:

    * the source file containing the reference is in ``changed`` (you
      added or modified that line on this branch), OR
    * the missing target's path (or its anchor-bearing file) is in
      ``deleted`` (you removed the target on this branch).
    """
    if not changed and not deleted:
        return {}
    out: dict[str, list[dict]] = {}
    for tgt, sources in stale_by_target.items():
        bare_tgt = tgt.split("#", 1)[0]
        target_deleted = bare_tgt in deleted
        kept_sources = []
        for s in sources:
            source_changed = s["file"].replace("\\", "/") in changed
            if source_changed or target_deleted:
                kept_sources.append(s)
        if kept_sources:
            out[tgt] = kept_sources
    return out


# ---------------------------------------------------------------------------
# Importance + recency ranking
# ---------------------------------------------------------------------------

_HIGH_PRIORITY_NAMES = (
    "README",
    "CHANGELOG",
    "CONTRIBUTING",
    "AGENTS",
    "CLAUDE",
    "GETTING-STARTED",
    "GETTING_STARTED",
    "INSTALL",
    "INSTALLATION",
    "DEPLOYMENT",
)
_HIGH_PRIORITY_DIR_PREFIXES = ("docs/", "doc/")
_LOW_PRIORITY_DIR_PREFIXES = (
    "templates/",
    "fixtures/",
    "test/fixtures/",
    "tests/fixtures/",
    "samples/",
    "examples/",
    "scripts/",
)


def _file_priority_weight(rel_path: str) -> float:
    """Subjective importance weight for a source file path.

    Hand-tuned to match the v12.48 dogfood: README/CHANGELOG/AGENTS/CLAUDE
    score 1.0, ``docs/*`` scores 0.85, generic source files score 0.5,
    ``templates/*`` and ``fixtures/*`` score 0.25 (sample artifacts that
    intentionally embed dangling references).
    """
    norm = rel_path.replace("\\", "/")
    base = os.path.basename(norm).split(".", 1)[0].upper()
    if base in _HIGH_PRIORITY_NAMES:
        return 1.0
    if any(norm.startswith(p) for p in _HIGH_PRIORITY_DIR_PREFIXES):
        return 0.85
    if any(norm.startswith(p) for p in _LOW_PRIORITY_DIR_PREFIXES):
        return 0.25
    return 0.5


def _recency_score(project_root: Path, rel_path: str, *, now: float) -> float:
    """Map source-file age to ``[0, 1]`` — recent = high.

    The map is intentionally coarse: <7 days → 1.0, <30 → 0.85, <90 →
    0.6, <365 → 0.4, else 0.2. We avoid mtime granularity sensitivity
    because git checkouts often touch every file's mtime.
    """
    try:
        mtime = (project_root / rel_path).stat().st_mtime
    except OSError:
        return 0.5
    days = max(0.0, (now - mtime) / 86400.0)
    if days < 7:
        return 1.0
    if days < 30:
        return 0.85
    if days < 90:
        return 0.6
    if days < 365:
        return 0.4
    return 0.2


def _rank_targets_priority(
    targets: list[tuple[str, list[dict]]],
    project_root: Path,
) -> list[tuple[str, list[dict]]]:
    """Sort by priority score = max(source priority × recency) × log(ref_count).

    We take the *max* across sources (one important README mention beats
    100 references in templates/) and bias toward larger absolute counts
    so a 30-ref hub doc still surfaces above a single ref in a peer doc.
    """
    import math

    now = time.time()

    def score(item: tuple[str, list[dict]]) -> float:
        _tgt, sources = item
        per_source = max(
            _file_priority_weight(s["file"]) * _recency_score(project_root, s["file"], now=now) for s in sources
        )
        return per_source * math.log2(1 + len(sources))

    return sorted(targets, key=lambda kv: (-score(kv), kv[0]))


# ---------------------------------------------------------------------------
# --fix machinery
# ---------------------------------------------------------------------------


def _build_fix_edits(
    targets: list[dict],
    project_root: Path,
) -> dict[str, list[dict]]:
    """Group HIGH-confidence rename rewrites by source file.

    The output structure is ``{source_file: [{line, raw, replacement, kind}]}``.
    Lines with multiple distinct stale refs (ambiguous edits) are
    skipped — better to leave them for human review than auto-rewrite
    one and risk a wrong substitution. Anchor-only findings are also
    skipped because there's no rename target to substitute.
    """
    by_source: dict[str, list[dict]] = defaultdict(list)
    for item in targets:
        hint = item.get("hint") or {}
        if hint.get("confidence") != "HIGH":
            continue
        new_target = hint.get("target")
        if not new_target:
            continue
        for s in item["sources"]:
            if s.get("kind") == "anchor":
                continue
            by_source[s["file"]].append(
                {
                    "line": s["line"],
                    "raw": s["raw"],
                    "replacement": new_target,
                    "kind": s["kind"],
                }
            )
    # Drop ambiguous lines: same file+line with multiple distinct raw values.
    cleaned: dict[str, list[dict]] = {}
    for src, edits in by_source.items():
        seen_at_line: dict[int, set[str]] = defaultdict(set)
        for e in edits:
            seen_at_line[e["line"]].add(e["raw"])
        kept = [e for e in edits if len(seen_at_line[e["line"]]) == 1]
        if kept:
            cleaned[src] = sorted(kept, key=lambda e: e["line"])
    return cleaned


def _apply_fix_to_text(content: str, edits: list[dict]) -> tuple[str, int]:
    """Apply edits to *content* and return ``(new_content, applied_count)``.

    Substitution is per-line and replaces the first occurrence of the
    raw URL on that line — markdown link syntax wraps the URL in
    parens/brackets, so a substring replace is safe in practice. We do
    NOT touch the link-text portion ``[text]`` — only the URL.
    """
    if not edits:
        return content, 0
    by_line: dict[int, list[dict]] = defaultdict(list)
    for e in edits:
        by_line[e["line"]].append(e)
    new_lines = []
    applied = 0
    for idx, line in enumerate(content.splitlines(keepends=True), start=1):
        if idx in by_line:
            mutated = line
            for e in by_line[idx]:
                if e["raw"] in mutated:
                    mutated = mutated.replace(e["raw"], e["replacement"], 1)
                    applied += 1
            new_lines.append(mutated)
        else:
            new_lines.append(line)
    return "".join(new_lines), applied


def _render_fix_diff(
    project_root: Path,
    edits_by_source: dict[str, list[dict]],
) -> tuple[str, int, int]:
    """Build a unified-diff preview of all proposed fixes.

    Returns ``(diff_text, files_touched, edits_planned)``. ``edits_planned``
    counts edit records, ``files_touched`` counts distinct source files.
    """
    import difflib

    chunks: list[str] = []
    files_touched = 0
    edits_planned = 0
    for src, edits in sorted(edits_by_source.items()):
        full = project_root / src
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                original = fh.read()
        except OSError:
            continue
        new_content, applied = _apply_fix_to_text(original, edits)
        if applied == 0 or new_content == original:
            continue
        files_touched += 1
        edits_planned += applied
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{src}",
            tofile=f"b/{src}",
            n=2,
        )
        chunks.append("".join(diff))
    return "\n".join(chunks), files_touched, edits_planned


def _apply_fixes_in_place(
    project_root: Path,
    edits_by_source: dict[str, list[dict]],
) -> tuple[int, int]:
    """Rewrite each source file with its edits.

    Returns ``(files_written, edits_applied)``. Files where the edit
    would be a no-op (raw URL no longer appears verbatim) are skipped
    silently — that's fine; the next scan will pick them up if still
    relevant.
    """
    files_written = 0
    edits_applied = 0
    for src, edits in edits_by_source.items():
        full = project_root / src
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                original = fh.read()
        except OSError:
            continue
        new_content, applied = _apply_fix_to_text(original, edits)
        if applied == 0 or new_content == original:
            continue
        try:
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(new_content)
        except OSError:
            continue
        files_written += 1
        edits_applied += applied
    return files_written, edits_applied


def _compact_sources(sources: list[dict]) -> list[dict]:
    """Group sources by source file so one file with N refs renders as one row."""
    by_file: dict[str, dict] = {}
    for s in sources:
        entry = by_file.setdefault(
            s["file"],
            {"file": s["file"], "lines": [], "kinds": set(), "raws": []},
        )
        entry["lines"].append(s["line"])
        entry["kinds"].add(s["kind"])
        entry["raws"].append(s["raw"])
    out = []
    for entry in by_file.values():
        entry["lines"] = sorted(set(entry["lines"]))
        entry["kinds"] = sorted(entry["kinds"])
        out.append(entry)
    out.sort(key=lambda e: (-len(e["lines"]), e["file"]))
    return out


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@click.command("stale-refs")
@click.option(
    "--limit",
    type=int,
    default=20,
    show_default=True,
    help="Maximum number of missing targets to display.",
)
@click.option(
    "--rename-hint/--no-rename-hint",
    default=True,
    show_default=True,
    help="Suggest a likely rename target by matching basenames against existing files.",
)
@click.option(
    "--gate",
    is_flag=True,
    default=False,
    help="Exit with code 5 when any stale ref is found (for CI / pre-commit).",
)
@click.option(
    "--kind",
    "kinds",
    multiple=True,
    type=click.Choice(["md_inline", "md_reference", "html_attr", "backtick"]),
    help="Restrict to specific reference kinds (repeatable). Default: all.",
)
@click.option(
    "--check-absolute-routes",
    is_flag=True,
    default=False,
    help=(
        "Also check absolute-path URLs without file extensions "
        '(e.g. href="/setup"). Off by default — those are usually '
        "static-site router paths, not file references."
    ),
)
@click.option(
    "--ignore",
    "ignore_source",
    multiple=True,
    metavar="GLOB",
    help=(
        "Glob pattern of source files to skip (e.g. 'CHANGELOG.md', "
        "'docs/legacy/*.md'). Repeatable. Useful to suppress historical "
        "documents that intentionally mention deleted files."
    ),
)
@click.option(
    "--ignore-target",
    "ignore_target",
    multiple=True,
    metavar="GLOB",
    help=(
        "Glob pattern of missing-target paths to suppress. Repeatable. "
        "Use to silence specific known-absent files (e.g. 'AGENTS.md')."
    ),
)
@click.option(
    "--by-file",
    is_flag=True,
    default=False,
    help=(
        "Group findings by source file instead of by missing target. "
        "Useful when you want to fix all dangling refs in one document at a time."
    ),
)
@click.option(
    "--no-anchors",
    "no_anchors",
    is_flag=True,
    default=False,
    help=(
        "Skip markdown anchor validation. By default, references like "
        "``docs/foo.md#missing-section`` are flagged when the file exists "
        "but the anchor doesn't. Disable when scanning repos with heavy "
        "non-standard anchor flavours (Hugo shortcodes, etc.)."
    ),
)
@click.option(
    "--diff",
    "diff_base",
    metavar="REF",
    default=None,
    is_flag=False,
    flag_value="",
    help=(
        "Only report findings new in this branch — sourced from changed "
        "files OR targeting newly-deleted files since merge-base with REF. "
        "Pass --diff bare to auto-pick (origin/main → main → master → "
        "HEAD~1). Pass --diff main / --diff origin/develop / --diff <sha> "
        "to specify. Makes ``--gate`` practical on repos with historical "
        "broken-ref noise."
    ),
)
@click.option(
    "--sort-by",
    type=click.Choice(["priority", "ref-count", "alpha"]),
    default="priority",
    show_default=True,
    help=(
        "Order findings by `priority` (importance × recency × ref count, "
        "default), `ref-count` (most-referenced first), or `alpha` "
        "(target path alphabetical)."
    ),
)
@click.option(
    "--fix",
    type=click.Choice(["preview", "apply"]),
    default=None,
    help=(
        "Auto-rename HIGH-confidence findings. ``preview`` prints a unified "
        "diff and exits 0; ``apply`` rewrites files in place. Only edits "
        "lines with a single unambiguous stale reference."
    ),
)
@click.pass_context
def stale_refs(
    ctx,
    limit,
    rename_hint,
    gate,
    kinds,
    check_absolute_routes,
    ignore_source,
    ignore_target,
    by_file,
    no_anchors,
    diff_base,
    sort_by,
    fix,
):
    """Find dangling file references — markdown links, HTML href/src, backtick paths.

    Scans every text file in the repo, extracts references that look like
    local file paths (``[text](path)``, ``href="path"``, `` `path/file.md` ``),
    and flags ones whose target no longer exists on disk. Also validates
    markdown ``#anchor`` fragments — ``[deploy](docs/cd.md#cloudflare)``
    is flagged when the file exists but the anchor doesn't.

    Three layers of intelligence sit on top of the scanner:

    * **Confidence-tagged rename hints** — git-history renames are HIGH
      confidence; basename matches are HIGH/MEDIUM/LOW depending on
      uniqueness; symbol-graph similarity fills in when the index exists.
    * **Branch-diff mode** — ``--diff`` filters findings to only those
      new in the current branch (introduced refs OR newly-deleted
      targets), so ``--gate`` becomes practical on repos with historical
      CHANGELOG noise.
    * **Auto-fix** — ``--fix preview`` shows a unified diff,
      ``--fix apply`` rewrites in place, but only for HIGH-confidence
      hints with unambiguous edits.

    Use ``--gate`` in CI to fail builds when docs / READMEs / configs
    reference deleted files. Pure filesystem scan — no index required.
    See also: ``roam doc-staleness`` (stale docstring content) and
    ``roam docs-coverage`` (missing public-symbol docs).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    include_excluded = ctx.obj.get("include_excluded", False) if ctx.obj else False

    project_root = find_project_root()
    t_start = time.perf_counter()
    stale_by_target, files_scanned, refs_seen, basename_idx = _scan_project(
        project_root,
        include_excluded=include_excluded,
        check_absolute_routes=check_absolute_routes,
        ignore_source=tuple(ignore_source),
        ignore_target=tuple(ignore_target),
        check_anchors=not no_anchors,
    )
    scan_seconds = round(time.perf_counter() - t_start, 3)

    # ---- Filter pipeline (kind → diff → sort)
    if kinds:
        kept: dict[str, list[dict]] = {}
        for tgt, sources in stale_by_target.items():
            filtered = [s for s in sources if s["kind"] in kinds]
            if filtered:
                kept[tgt] = filtered
        stale_by_target = kept

    diff_info: dict[str, str] = {}
    diff_warning: str | None = None
    if diff_base is not None:
        base_sha = _resolve_diff_base(project_root, diff_base)
        if base_sha is None:
            diff_warning = (
                "Warning: --diff requested but no merge-base resolved (no git? unknown ref?). Skipping branch filter."
            )
        else:
            change_info = _git_changed_files(project_root, base_sha)
            if change_info is None:
                diff_warning = "Warning: --diff failed to compute changed files. Skipping branch filter."
            else:
                changed, deleted = change_info
                stale_by_target = _filter_diff_targets(stale_by_target, changed, deleted)
                diff_info = {
                    "base_sha": base_sha,
                    "base_ref": diff_base or "auto",
                    "changed_files": str(len(changed)),
                    "deleted_files": str(len(deleted)),
                }

    if sort_by == "ref-count":
        sorted_targets = sorted(stale_by_target.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    elif sort_by == "alpha":
        sorted_targets = sorted(stale_by_target.items(), key=lambda kv: kv[0])
    else:
        sorted_targets = _rank_targets_priority(list(stale_by_target.items()), project_root)

    total_refs = sum(len(v) for v in stale_by_target.values())
    target_count = len(stale_by_target)
    anchor_findings = sum(1 for sources in stale_by_target.values() if sources and sources[0]["kind"] == "anchor")

    # ---- Build hint context once; providers memoise within it
    hint_ctx = HintContext(project_root=project_root, basename_idx=basename_idx)

    def _build_item(tgt: str, sources: list[dict], *, all_sources: bool) -> dict:
        item: dict = {
            "target": tgt,
            "ref_count": len(sources),
            "sources": sources if all_sources else sources[:10],
        }
        if rename_hint:
            hint = _hint_for_target(tgt, sources, hint_ctx)
            if hint is not None:
                item["hint"] = hint
                # Backwards-compat: keep the old flat ``rename_hint`` field.
                item["rename_hint"] = hint["target"]
        return item

    full_targets_with_hints = [_build_item(tgt, srcs, all_sources=True) for tgt, srcs in sorted_targets]
    displayed = [_build_item(tgt, srcs, all_sources=False) for tgt, srcs in sorted_targets[:limit]]

    if target_count == 0:
        verdict = f"all refs resolve · {refs_seen} checked · {files_scanned} files · {scan_seconds}s"
    else:
        anchor_note = f" · {anchor_findings} anchor" if anchor_findings else ""
        diff_note = f" · diff base {diff_info['base_sha'][:7]}" if diff_info else ""
        verdict = (
            f"{total_refs} stale ref(s) · {target_count} missing target(s){anchor_note}{diff_note} · "
            f"{refs_seen} refs checked · {files_scanned} files · {scan_seconds}s"
        )

    # ---- --fix mode short-circuits all other output paths
    if fix is not None:
        edits_by_source = _build_fix_edits(full_targets_with_hints, project_root)
        if fix == "preview":
            diff_text, files_touched, edits_planned = _render_fix_diff(project_root, edits_by_source)
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "stale-refs",
                            summary={
                                "verdict": (
                                    f"--fix preview: {edits_planned} edit(s) "
                                    f"across {files_touched} file(s) "
                                    f"({target_count} missing target(s) total)"
                                ),
                                "fix_mode": "preview",
                                "edits_planned": edits_planned,
                                "files_touched": files_touched,
                                "missing_targets": target_count,
                                "stale_refs": total_refs,
                                "scan_seconds": scan_seconds,
                            },
                            diff=diff_text,
                        )
                    )
                )
            else:
                click.echo(
                    f"VERDICT: --fix preview · {edits_planned} edit(s) across "
                    f"{files_touched} file(s) (HIGH-confidence hints only)\n"
                )
                if not edits_planned:
                    click.echo("No HIGH-confidence rename hints to apply.")
                else:
                    click.echo(diff_text or "(empty diff)")
            return
        # fix == "apply"
        files_written, edits_applied = _apply_fixes_in_place(project_root, edits_by_source)
        msg = f"--fix apply · wrote {edits_applied} edit(s) to {files_written} file(s)"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "stale-refs",
                        summary={
                            "verdict": msg,
                            "fix_mode": "apply",
                            "edits_applied": edits_applied,
                            "files_written": files_written,
                            "missing_targets": target_count,
                            "stale_refs": total_refs,
                            "scan_seconds": scan_seconds,
                        },
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {msg}")
            if files_written:
                click.echo("Re-run `roam stale-refs` to confirm and address remaining findings.")
            else:
                click.echo("No HIGH-confidence rename hints applied (nothing to do).")
        return

    if sarif_mode:
        from roam.output.sarif import stale_refs_to_sarif, write_sarif

        click.echo(write_sarif(stale_refs_to_sarif(full_targets_with_hints)))
        if gate and target_count > 0:
            ctx.exit(5)
        return

    if json_mode:
        summary: dict = {
            "verdict": verdict,
            "missing_targets": target_count,
            "stale_refs": total_refs,
            "files_scanned": files_scanned,
            "refs_checked": refs_seen,
            "displayed": len(displayed),
            "scan_seconds": scan_seconds,
            "sort_by": sort_by,
            "anchor_findings": anchor_findings,
        }
        if diff_info:
            summary["diff_base"] = diff_info["base_sha"]
            summary["diff_base_ref"] = diff_info["base_ref"]
            summary["diff_changed_files"] = int(diff_info["changed_files"])
            summary["diff_deleted_files"] = int(diff_info["deleted_files"])
        if diff_warning:
            summary["diff_warning"] = diff_warning
        click.echo(
            to_json(
                json_envelope(
                    "stale-refs",
                    summary=summary,
                    targets=displayed,
                )
            )
        )
        if gate and target_count > 0:
            ctx.exit(5)
        return

    # --- Text output ---
    if diff_warning:
        click.echo(diff_warning, err=True)
    click.echo(f"VERDICT: {verdict}\n")
    if target_count == 0:
        click.echo(f"Scanned {files_scanned} files, checked {refs_seen} references — all targets exist.")
        return

    if by_file:
        by_source: dict[str, list[dict]] = defaultdict(list)
        for tgt, sources in sorted_targets:
            for s in sources:
                by_source[s["file"]].append(
                    {
                        "target": tgt,
                        "line": s["line"],
                        "kind": s["kind"],
                        "raw": s["raw"],
                    }
                )
        sorted_files = sorted(by_source.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        click.echo(f"Stale references by source file (top {min(limit, len(sorted_files))} of {len(sorted_files)}):\n")
        for source_file, refs in sorted_files[:limit]:
            click.echo(f"  {source_file}  ({len(refs)} stale ref{'s' if len(refs) != 1 else ''})")
            for r in refs[:8]:
                click.echo(f"    L{r['line']}  [{r['kind']}]  → {r['target']}")
            if len(refs) > 8:
                click.echo(f"    (+{len(refs) - 8} more)")
            click.echo()
        if len(sorted_files) > limit:
            click.echo(f"  (+{len(sorted_files) - limit} more source files, raise --limit to see all)")
        click.echo(f"  Total: {total_refs} stale ref(s) across {len(sorted_files)} source file(s).")
        if gate and target_count > 0:
            ctx.exit(5)
        return

    click.echo(f"Stale references (top {len(displayed)} of {target_count} missing targets, sorted by {sort_by}):\n")
    for item in displayed:
        ref_word = "ref" if item["ref_count"] == 1 else "refs"
        click.echo(f"  {item['target']}  ({item['ref_count']} {ref_word})")
        hint = item.get("hint")
        if hint:
            click.echo(f"    → did you mean {hint['target']}? [{hint['confidence']} · {hint['reason']}]")
        compacted = _compact_sources(item["sources"])
        for entry in compacted[:5]:
            lines = ",".join(str(n) for n in entry["lines"])
            kind_str = ",".join(entry["kinds"])
            click.echo(f"    {entry['file']}:{lines}  [{kind_str}]")
        if len(compacted) > 5:
            extra_files = len(compacted) - 5
            click.echo(f"    (+{extra_files} more source file{'s' if extra_files != 1 else ''})")
        click.echo()

    if target_count > limit:
        click.echo(f"  (+{target_count - limit} more missing targets, raise --limit to see all)")
    click.echo(f"  Total: {total_refs} stale ref(s) across {target_count} missing target(s) in {files_scanned} files.")

    if gate and target_count > 0:
        ctx.exit(5)
