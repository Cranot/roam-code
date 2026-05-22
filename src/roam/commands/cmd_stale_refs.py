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
import json
import os
import re
import subprocess
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

import click

from roam.atomic_io import atomic_write_text
from roam.capability import roam_capability
from roam.commands.next_steps import format_next_steps_text, suggest_next_steps
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
    """Drop wrapping ``<...>``, trailing punctuation, ``#fragment`` / ``?query``,
    and percent-decode the result.

    URL-encoding matters here: a markdown link to a file with spaces in
    its name is conventionally written ``[x](docs/file%20with%20spaces.md)``,
    and the file on disk is named ``file with spaces.md``. Without
    decoding we'd flag every such reference as missing.
    """
    url = url.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    url = url.rstrip(",.;:")
    # Fragments may themselves contain '?', so split on '#' first.
    url = url.split("#", 1)[0]
    url = url.split("?", 1)[0]
    # Percent-decode after structural decoration is gone. ``unquote`` is
    # safe on already-decoded input (idempotent on text without ``%XX``
    # sequences).
    try:
        url = urllib.parse.unquote(url)
    except Exception:
        pass
    return url


def _extract_fragment(url: str) -> str:
    """Return the bare ``#anchor`` slug, or ``""`` when absent.

    Mirrors :func:`_strip_url_decorations` — same wrapping/whitespace
    rules — so the path resolver and the anchor validator see consistent
    raw inputs from one call. Fragments are also percent-decoded so a
    reference to ``#caf%C3%A9`` matches a header slug ``café``.
    """
    url = url.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    url = url.rstrip(",.;:")
    if "#" not in url:
        return ""
    fragment = url.split("#", 1)[1]
    fragment = fragment.split("?", 1)[0]
    try:
        fragment = urllib.parse.unquote(fragment)
    except Exception:
        pass
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


def _extract_refs(
    content: str,
    *,
    prose_mode: bool,
    scan_bare_backticks: bool = False,
) -> list[tuple[int, str, str]]:
    """Extract ``(line_number, kind, url)`` triples from one file's text.

    Line numbers are 1-based. ``kind`` is one of ``md_inline``,
    ``md_reference``, ``html_attr``, ``backtick``.

    When *prose_mode* is ``False`` (source-code file), only backtick paths
    are extracted — markdown link syntax collides with regex character
    classes (``[^'"]+`` etc.) in code and produces a flood of false
    positives.

    When *scan_bare_backticks* is ``False`` (the default), bare
    backtick-wrapped paths in prose (e.g. `` `MyController.php` ``) are
    NOT treated as filesystem references. They're inline code, not
    structured link syntax — treating them as path claims produces ~39%
    false-positive rate on real repos (see Bug 2 in the dogfood
    findings).

    Even when *scan_bare_backticks* is ``True``, backtick matches that
    fall INSIDE the ``[display]`` portion of a markdown inline link are
    suppressed. Otherwise ``[`code-map/X.md`](../code-map/X.md)`` would
    extract both the display-string backtick AND the URL, and the
    display half — never a real path — would silently corrupt the
    rename-hint pipeline (Bug 1).
    """
    refs: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        # Collect markdown inline link spans first so we can suppress
        # any nested backtick matches that fall inside their display
        # text. Each span is a half-open ``(text_start, text_end)`` over
        # the ``[display]`` portion, NOT the whole ``[display](url)``
        # construct — backticks in the URL half are already a different
        # extraction shape and don't collide here.
        md_text_spans: list[tuple[int, int]] = []
        if prose_mode:
            for m in _MD_INLINE_RE.finditer(line):
                refs.append((lineno, "md_inline", m.group("url")))
                # Record the inner [text] span (between '[' and ']').
                # ``m.start()`` points at the leading ``!`` / ``[``; the
                # text group's span gives us the precise display range.
                t_start, t_end = m.span("text")
                md_text_spans.append((t_start, t_end))
            m_ref = _MD_REFERENCE_RE.match(line)
            if m_ref:
                refs.append((lineno, "md_reference", m_ref.group("url")))
            for m in _HTML_ATTR_RE.finditer(line):
                url = m.group("v1") if m.group("v1") is not None else m.group("v2")
                if url:
                    refs.append((lineno, "html_attr", url))
        # Bare backtick scanning is opt-in by default. In source-code
        # files (non-prose), keep the historical behaviour: extract them
        # so test fixtures referencing renamed files keep working — the
        # placeholder filter in _resolve_backtick_target already trims
        # the noise there.
        if scan_bare_backticks or not prose_mode:
            for m in _BACKTICK_PATH_RE.finditer(line):
                # Bug 1 suppression: skip backticks whose match falls
                # ENTIRELY within a markdown-link's display text. The
                # URL half is the source of truth for liveness; the
                # display half is cosmetic and may legitimately look
                # like a path that doesn't exist relative to the source
                # file's directory.
                bt_start = m.start("path")
                bt_end = m.end("path")
                if any(s <= bt_start and bt_end <= e for s, e in md_text_spans):
                    continue
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


def basename_idx_paths(basename_idx: dict[str, list[str]]) -> list[str]:
    """Flatten a basename → [paths] map back to a list of repo-relative paths.

    Used by the ``--with-candidates`` JSON path to surface repo files for
    the MCP-side LLM enricher to choose from.
    """
    out: list[str] = []
    for paths in basename_idx.values():
        out.extend(paths)
    return out


def _is_prose_path(rel_path: str) -> bool:
    """Heuristic: is this path the kind of file users typically link to in docs?

    Used to up-weight prose-shaped paths in the ``--with-candidates`` sample
    so the LLM enricher has them in front of it.
    """
    ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    return ext in {"md", "markdown", "rst", "txt", "html", "htm"} or "/" not in rel_path


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob with ``**`` recursion into a ``re.Pattern``.

    fnmatch.fnmatchcase treats ``**`` as a single-segment wildcard,
    surprising users typing ``docs/**/*.md`` who expect recursive
    descent. We compile a small custom translator:

    * ``**`` → matches any number of path segments (zero or more,
      including no path component at all). ``docs/**/*.md`` matches
      ``docs/foo.md`` AND ``docs/sub/foo.md`` AND ``docs/a/b/c.md``.
    * ``*`` → matches any sequence of non-separator chars in one
      segment.
    * ``?`` → matches any single non-separator char.
    * Other regex meta-chars are escaped.

    We cache compiled patterns at the call site (LRU) for hot-path
    perf; the translation itself is one-shot.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*" and i + 1 < len(pattern) and pattern[i + 1] == "*":
            # ``**`` consumes any remaining slashes optionally too — the
            # idiomatic ``docs/**/*.md`` would otherwise need a literal
            # ``docs/foo.md`` to match ``foo.md`` directly under docs.
            # We absorb a trailing slash if present.
            if i + 2 < len(pattern) and pattern[i + 2] == "/":
                out.append(r"(?:.*/)?")
                i += 3
            else:
                out.append(r".*")
                i += 2
        elif c == "*":
            out.append(r"[^/]*")
            i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches_any_glob(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """Return True when *rel_path* matches any glob in *patterns*.

    Two pattern flavours, picked per-pattern based on shape:

    * **Patterns containing ``**``** use a segment-aware translator:
      ``*`` matches one segment, ``**`` matches any depth.
      ``docs/**/*.md`` correctly matches every ``.md`` under ``docs/``
      at any depth.
    * **Patterns without ``**``** use ``fnmatch.fnmatchcase`` for
      backwards compatibility — substring-style ``*foo*`` patterns and
      single-segment ``docs/old/*`` patterns behave the same way they
      always have.

    The split avoids breaking pre-existing patterns while unlocking
    recursive-glob semantics for users who explicitly type ``**``.
    """
    if not patterns:
        return False
    norm = rel_path.replace("\\", "/")
    for p in patterns:
        normalised = p.replace("\\", "/")
        if "**" in normalised:
            if _glob_to_regex(normalised).match(norm):
                return True
        else:
            if fnmatch.fnmatchcase(norm, normalised):
                return True
    return False


def _scan_project(
    project_root: Path,
    *,
    include_excluded: bool,
    check_absolute_routes: bool = False,
    ignore_source: tuple[str, ...] = (),
    ignore_target: tuple[str, ...] = (),
    check_anchors: bool = True,
    scan_bare_backticks: bool = False,
) -> tuple[dict[str, list[dict]], int, int, dict[str, list[str]], AnchorCache]:
    """Walk the repo and collect every reference.

    Returns ``(stale_by_target, files_scanned, refs_seen, basename_idx, anchor_cache)``.

    * ``stale_by_target`` maps the resolved missing target (relative path
      string) to a list of source records
      ``{file, line, kind, raw, anchor?}``. The optional ``anchor`` key
      is populated when the finding is anchor-only (file exists but
      ``#fragment`` doesn't); the synthetic target string in that case
      is ``"<file>#<anchor>"`` so multi-anchor failures inside the same
      file still group cleanly.
    * ``basename_idx`` is the basename → [paths] map, reusable by the
      caller for rename hints without re-running discovery.
    * ``anchor_cache`` is the :class:`AnchorCache` populated during the
      scan; the caller reuses it for "did you mean" anchor hints
      without re-parsing target files.
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

        for lineno, kind, raw_url in _extract_refs(
            content,
            prose_mode=prose_mode,
            scan_bare_backticks=scan_bare_backticks,
        ):
            refs_seen += 1
            fragment = _extract_fragment(raw_url) if kind != "backtick" else ""

            # In-page anchor refs (``[x](#section)`` with no path) validate
            # against the SOURCE file's own header set. Without this branch
            # the path resolver returns None and the reference is silently
            # accepted — so ``[broken](#nonexistent)`` in a README would
            # never be flagged. We run the check before the path resolver
            # because a pure-anchor URL has no path to resolve.
            if kind != "backtick" and fragment and not _strip_url_decorations(raw_url).split("#", 1)[0]:
                if check_anchors and AnchorCache.is_anchor_validatable(rel):
                    anchors = anchor_cache.anchors_for(rel)
                    if anchors is not None and fragment.lower() not in anchors:
                        synthetic = f"{rel}#{fragment}"
                        if _matches_any_glob(synthetic, ignore_target) or _matches_any_glob(rel, ignore_target):
                            continue
                        stale_by_target[synthetic].append(
                            {
                                "file": rel,
                                "line": lineno,
                                "kind": "anchor",
                                "raw": raw_url,
                                "anchor": fragment,
                                "anchor_target_file": rel,
                            }
                        )
                continue

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
                    # Case-insensitive lookup — GitHub matches ``#Setup``
                    # against header ``# Setup``. Stored slugs are
                    # lowercased; lowercase the URL fragment too.
                    if anchors is not None and fragment.lower() not in anchors:
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

    return stale_by_target, files_scanned, refs_seen, basename_idx, anchor_cache


_ANCHOR_DID_YOU_MEAN_THRESHOLD = 0.6


def _slug_tokens(slug: str) -> set[str]:
    """Split a slug into its dash-separated tokens, dropping empties."""
    return {tok for tok in slug.split("-") if tok}


def _closest_anchor_hint(
    missing_anchor: str,
    candidate_anchors: set[str],
    *,
    threshold: float = _ANCHOR_DID_YOU_MEAN_THRESHOLD,
) -> tuple[str, float] | None:
    """Return ``(closest_slug, similarity)`` if any candidate clears the threshold.

    Combines two signals and takes the max:

    * **Character ratio** (:class:`difflib.SequenceMatcher`) — strong on
      typo / dropped-hyphen / plural drift: ``mcp-server`` ↔
      ``mcp-servers`` scores ~0.95.
    * **Token Jaccard** (intersection/union over dash-separated tokens)
      — strong on word-reorder drift: ``docker-setup`` ↔ ``setup-with-docker``
      scores 2/3 ≈ 0.67 even though the character ratio is mediocre.

    The max of the two is returned because the two signals catch
    different drift patterns; we want either to fire.
    """
    import difflib

    if not candidate_anchors:
        return None
    needle = missing_anchor.lower()
    needle_tokens = _slug_tokens(needle)
    best_slug: str | None = None
    best_score = 0.0
    for anchor in candidate_anchors:
        char_ratio = difflib.SequenceMatcher(None, needle, anchor).ratio()
        # Token Jaccard. Only meaningful when both slugs have tokens.
        anchor_tokens = _slug_tokens(anchor)
        token_score = 0.0
        if needle_tokens and anchor_tokens:
            inter = len(needle_tokens & anchor_tokens)
            union = len(needle_tokens | anchor_tokens)
            token_score = inter / union
        score = max(char_ratio, token_score)
        if score > best_score:
            best_score = score
            best_slug = anchor
    if best_slug is None or best_score < threshold:
        return None
    return best_slug, best_score


def _hint_for_target(
    rel_target: str,
    sources: list[dict],
    hint_ctx: HintContext,
    *,
    anchor_cache: AnchorCache | None = None,
) -> dict | None:
    """Return a rich hint dict for a missing target, or ``None``.

    Two flavours:

    * **Path-finding hints** (regular dangling-path) consult the provider
      chain (``git-history`` → ``symbol-graph`` → ``basename``) for a
      rename target.
    * **Anchor-finding hints** (target file exists, ``#fragment`` missing)
      look up the closest existing anchor in the same file via
      :func:`_closest_anchor_hint`. Confidence is HIGH when similarity
      is ≥ 0.85, MEDIUM otherwise. The hint ``target`` is the suggested
      ``file#anchor`` rewrite, so consumers can pattern-match the same
      shape they'd see on a path-finding rename.
    """
    if sources and sources[0].get("kind") == "anchor":
        if anchor_cache is None:
            return None
        first_source = sources[0]
        anchor_file = first_source.get("anchor_target_file") or rel_target.split("#", 1)[0]
        missing_anchor = first_source.get("anchor", "")
        if not missing_anchor:
            return None
        anchors = anchor_cache.anchors_for(anchor_file)
        if not anchors:
            return None
        match = _closest_anchor_hint(missing_anchor, anchors)
        if match is None:
            return None
        suggested_slug, score = match
        confidence = "HIGH" if score >= 0.85 else "MEDIUM"
        return {
            "target": f"{anchor_file}#{suggested_slug}",
            "confidence": confidence,
            "reason": f"closest anchor in same file (similarity {score:.2f})",
            "source": "anchor-similarity",
        }
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


def _recency_score(
    project_root: Path,
    rel_path: str,
    *,
    now: float,
    cache: dict[str, float] | None = None,
) -> float:
    """Map source-file age to ``[0, 1]`` — recent = high.

    The map is intentionally coarse: <7 days → 1.0, <30 → 0.85, <90 →
    0.6, <365 → 0.4, else 0.2. We avoid mtime granularity sensitivity
    because git checkouts often touch every file's mtime.

    When *cache* is provided it memoises the resolved score per
    *rel_path* — useful inside a sort key where the same source file
    appears under many missing-target groups (without it we'd ``stat``
    the same path dozens of times).
    """
    if cache is not None and rel_path in cache:
        return cache[rel_path]
    try:
        mtime = (project_root / rel_path).stat().st_mtime
    except OSError:
        score = 0.5
        if cache is not None:
            cache[rel_path] = score
        return score
    days = max(0.0, (now - mtime) / 86400.0)
    if days < 7:
        score = 1.0
    elif days < 30:
        score = 0.85
    elif days < 90:
        score = 0.6
    elif days < 365:
        score = 0.4
    else:
        score = 0.2
    if cache is not None:
        cache[rel_path] = score
    return score


def _rank_targets_priority(
    targets: list[tuple[str, list[dict]]],
    project_root: Path,
) -> list[tuple[str, list[dict]]]:
    """Sort by priority score = max(source priority × recency) × log(ref_count).

    We take the *max* across sources (one important README mention beats
    100 references in templates/) and bias toward larger absolute counts
    so a 30-ref hub doc still surfaces above a single ref in a peer doc.
    The recency cache is shared across every score evaluation so each
    distinct source file is ``stat``'d at most once per sort.
    """
    import math

    now = time.time()
    recency_cache: dict[str, float] = {}

    def score(item: tuple[str, list[dict]]) -> float:
        _tgt, sources = item
        per_source = max(
            _file_priority_weight(s["file"]) * _recency_score(project_root, s["file"], now=now, cache=recency_cache)
            for s in sources
        )
        return per_source * math.log2(1 + len(sources))

    return sorted(targets, key=lambda kv: (-score(kv), kv[0]))


# ---------------------------------------------------------------------------
# --fix machinery
# ---------------------------------------------------------------------------


def _has_repeated_segment_run(rel_path: str) -> bool:
    """True when *rel_path* contains the same directory segment run twice in
    sequence (e.g. ``docs/legacy/docs/legacy/X.md``).

    Belt-and-suspenders defense against Bug 1: if the rename-hint
    pipeline ever proposes a path that resolves to a self-repeating
    directory chain, refuse the edit. Real repos almost never have
    such a layout; a proposed rewrite that produces one is almost
    certainly the result of the display-string-as-path bug or a
    cousin of it.

    Detection strategy: find ANY pair of adjacent segment runs where
    a run of length N >= 2 immediately repeats. ``docs/legacy/docs/legacy``
    matches (run ``docs/legacy`` followed by ``docs/legacy``). A single
    repeated segment like ``docs/docs/X.md`` ALSO matches (run of
    length 1 at the start). We err on the side of refusal because the
    legitimate-repeat case (``test/test/`` directories in some test
    frameworks) is rare AND such a file would already exist on disk,
    in which case the rewrite would not be needed.
    """
    norm = rel_path.replace("\\", "/").strip("/")
    if not norm:
        return False
    segments = norm.split("/")
    n = len(segments)
    # Walk window sizes from 1..n//2; any window that matches the next
    # window of the same size is a repeat.
    for window in range(1, n // 2 + 1):
        for start in range(0, n - 2 * window + 1):
            if segments[start : start + window] == segments[start + window : start + 2 * window]:
                return True
    return False


def _rewrite_is_safe(
    source_file_rel: str,
    raw_url: str,
    replacement_url: str,
    project_root: Path,
) -> tuple[bool, str]:
    """Decide whether a proposed URL rewrite is safe to apply.

    Returns ``(safe, reason)``. ``reason`` is empty on success, a short
    human-readable explanation on refusal.

    The check runs three guards in order:

    1. **No-op detection.** If ``raw_url`` already resolves to a live
       target from the source file's directory, the rewrite is a
       net-negative (the original was fine). Catches Bug 1's "URL
       half was valid all along" case.
    2. **Double-prefix detection.** If the rewritten path contains a
       repeating directory chain (``docs/legacy/docs/legacy/``),
       refuse — see :func:`_has_repeated_segment_run`.
    3. **New-URL liveness.** The rewritten URL must resolve to an
       existing file on disk from the source's directory. If it
       doesn't, the rewrite is replacing one broken link with
       another, which is exactly what we want to avoid.

    Guards 1 and 3 are the load-bearing ones; guard 2 is defense in
    depth in case the resolver produces a path that "exists" in some
    unexpected manner (case-insensitive FS, symlink loops).
    """
    # Guard 1: original URL already resolved live → refuse.
    original_target = _resolve_target(raw_url, source_file_rel, project_root)
    if original_target is not None and original_target.exists():
        return False, "REFUSED: original URL already resolves to a live target"

    # Guard 2: rewrite produces a doubled-prefix path → refuse.
    new_target = _resolve_target(replacement_url, source_file_rel, project_root)
    if new_target is not None:
        try:
            rel_new = new_target.relative_to(project_root).as_posix()
        except ValueError:
            rel_new = str(new_target)
        if _has_repeated_segment_run(rel_new):
            return False, "REFUSED: would create double-prefix path"

    # Guard 3: rewrite must resolve to an existing file.
    if new_target is None or not new_target.exists():
        # Anchor-only rewrites are validated separately (the path
        # portion is unchanged and known-good); only enforce file
        # existence when the path portion actually changes. The
        # caller can pass the same value for raw and replacement
        # when only the fragment differs, in which case ``new_target``
        # still resolves (to the existing file) and we land in the
        # "exists" branch.
        return False, "REFUSED: rewritten URL does not resolve to an existing file"

    return True, ""


def _build_fix_edits(
    targets: list[dict],
    project_root: Path,
    *,
    include_medium: bool = False,
    refused_log: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Group HIGH-confidence (and optionally MEDIUM) rewrites by source file.

    When *include_medium* is True (CLI: ``--fix-medium``), MEDIUM hints
    flow through too. The user has explicitly opted into looser auto-fix.

    Output structure: ``{source_file: [{line, raw, replacement, kind}]}``.
    Lines with multiple distinct stale refs (ambiguous edits) are
    skipped — better to leave them for human review than auto-rewrite
    one and risk a wrong substitution.

    Two flavours of rewrite are produced:

    * **Path findings** (``kind`` ∈ ``{md_inline, md_reference,
      html_attr, backtick}``) substitute the path portion of the URL
      and **preserve any ``#fragment``** that was in the raw URL — so
      ``[x](old/foo.md#section)`` rewrites to
      ``[x](docs/foo.md#section)``, not ``[x](docs/foo.md)`` (which
      would silently drop the fragment).
    * **Anchor findings** (``kind == "anchor"``) substitute only the
      ``#fragment`` portion of the URL, preserving everything before
      the ``#``. Works for both cross-file refs (``docs/x.md#wrong`` →
      ``docs/x.md#correct``) and in-page refs (``#wrong`` →
      ``#correct``). This means HIGH-confidence anchor-similarity
      hints (e.g. ``#mcp-server`` → ``#mcp-servers``) ARE auto-fixable
      via ``--fix apply``.
    """
    by_source: dict[str, list[dict]] = defaultdict(list)
    accepted_confidence = {"HIGH"}
    if include_medium:
        accepted_confidence.add("MEDIUM")
    for item in targets:
        hint = item.get("hint") or {}
        if hint.get("confidence") not in accepted_confidence:
            continue
        new_target = hint.get("target")
        if not new_target:
            continue
        for s in item["sources"]:
            raw = s["raw"]
            if s.get("kind") == "anchor":
                # Anchor-only fix: substitute the fragment portion only.
                # The hint target is always ``file#anchor`` shape; we
                # extract the new fragment and rewrite the raw URL by
                # replacing ``#<old>`` with ``#<new>`` literally so any
                # surrounding decoration (path prefix, query string)
                # stays intact.
                if "#" not in new_target:
                    continue
                old_anchor = s.get("anchor") or ""
                new_anchor = new_target.split("#", 1)[1]
                if not old_anchor or not new_anchor or old_anchor == new_anchor:
                    continue
                old_marker = f"#{old_anchor}"
                if old_marker not in raw:
                    # Encoding mismatch (e.g. raw is ``#caf%C3%A9`` but
                    # the resolved anchor is decoded ``#café``). Skip
                    # rather than risk a wrong substitution.
                    continue
                replacement = raw.replace(old_marker, f"#{new_anchor}", 1)
                by_source[s["file"]].append(
                    {
                        "line": s["line"],
                        "raw": raw,
                        "replacement": replacement,
                        "kind": s["kind"],
                    }
                )
                continue
            # Path-finding fix: substitute the new path. Preserve any
            # fragment that was in the raw URL — dropping it would
            # silently break in-target navigation.
            replacement = new_target
            if "#" in raw and "#" not in replacement:
                fragment = raw.split("#", 1)[1]
                replacement = f"{replacement}#{fragment}"
            # Belt-and-suspenders safety: re-simulate the rewrite from
            # the source file's perspective and refuse the edit when:
            #   - the ORIGINAL URL already resolves live (Bug 1 case
            #     where the display string drove the false-positive),
            #   - the new URL would produce a double-prefix path,
            #   - the new URL doesn't resolve to an existing file.
            safe, reason = _rewrite_is_safe(s["file"], raw, replacement, project_root)
            if not safe:
                if refused_log is not None:
                    refused_log.append(
                        f"{reason} (source={s['file']} line={s['line']} raw={raw!r} replacement={replacement!r})"
                    )
                continue
            by_source[s["file"]].append(
                {
                    "line": s["line"],
                    "raw": raw,
                    "replacement": replacement,
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

    Substitution model:

    * Edits are deduplicated per line by ``(raw, replacement)`` so a line
      that legitimately has the SAME stale URL twice is rewritten in a
      single pass. Without dedup, an anchor rewrite where the new
      fragment is a SUPERSET of the old (``#mcp-server`` → ``#mcp-servers``)
      would compound — the second pass would re-match the old fragment
      inside the just-substituted ``#mcp-servers`` and turn it into
      ``#mcp-serverss``.
    * For each unique ``(raw, replacement)`` we use ``str.replace`` with
      no count limit so all occurrences of the raw URL on that line are
      rewritten atomically.
    * The reported ``applied`` count still reflects the per-finding
      total so callers' "N edits applied" stays meaningful.
    * Edits with identical ``raw == replacement`` are skipped — they're
      a no-op that would otherwise inflate the applied count.

    We do NOT touch the link-text portion ``[text]`` — only the URL.
    """
    if not edits:
        return content, 0
    # ``by_line[lineno][(raw, replacement)] = total_findings_collapsed``
    by_line: dict[int, dict[tuple[str, str], int]] = defaultdict(dict)
    for e in edits:
        if e["raw"] == e["replacement"]:
            continue
        key = (e["raw"], e["replacement"])
        by_line[e["line"]][key] = by_line[e["line"]].get(key, 0) + 1
    new_lines = []
    applied = 0
    for idx, line in enumerate(content.splitlines(keepends=True), start=1):
        if idx in by_line:
            mutated = line
            # Apply longer raws first to avoid wrecking longer URLs that
            # contain a shorter raw as a substring (e.g. a hypothetical
            # raw1 = ``foo`` vs raw2 = ``foobar`` on the same line).
            for (raw, replacement), count in sorted(by_line[idx].items(), key=lambda kv: -len(kv[0][0])):
                if raw in mutated:
                    mutated = mutated.replace(raw, replacement)
                    applied += count
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
) -> tuple[int, int, list[str]]:
    """Rewrite each source file with its edits, atomically.

    Returns ``(files_written, edits_applied, locked_files)``:

    * ``files_written`` / ``edits_applied`` — successful counts.
    * ``locked_files`` — paths that we couldn't read OR atomically
      replace because another process held them (Windows
      DELETE_PENDING semantics show up here regularly). Surfacing
      this list helps Windows users debug "why didn't my edits land?"
      without having to grep stderr.

    Writes use the tempfile-then-``os.replace`` pattern so an
    interrupted run cannot leave a half-written source file on disk.
    """
    files_written = 0
    edits_applied = 0
    locked_files: list[str] = []
    for src, edits in edits_by_source.items():
        full = project_root / src
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                original = fh.read()
        except OSError:
            locked_files.append(src)
            continue
        new_content, applied = _apply_fix_to_text(original, edits)
        if applied == 0 or new_content == original:
            continue
        # Windows DELETE_PENDING / locked-file errors must be recorded in
        # ``locked_files`` rather than aborting the whole batch — wrap the
        # canonical atomic_write_text (which raises) to swallow OSError.
        try:
            atomic_write_text(full, new_content)
        except OSError:
            locked_files.append(src)
            continue
        files_written += 1
        edits_applied += applied
    return files_written, edits_applied, locked_files


# ---------------------------------------------------------------------------
# External HTTP link checker
# ---------------------------------------------------------------------------

# Match http(s) URLs inside markdown link / HTML attribute / backtick text
# without bringing in additional regex complexity to the scanner. We DO
# tolerate leading ``<`` and trailing ``>`` (autolink form) because those
# show up frequently in markdown without being a separate kind.
_EXTERNAL_URL_RE = re.compile(
    r"!?\[(?P<text>[^\]\n]*)\]\(<?(?P<url>https?://[^)\s>]+)>?\)"
    r"|(?:href|src)=(?:\"(?P<v1>https?://[^\"]+)\"|'(?P<v2>https?://[^']+)')"
    r"|<(?P<auto>https?://[^>\s]+)>"
)


def _extract_external_urls(content: str) -> list[tuple[int, str]]:
    """Return ``[(lineno, url)]`` for every external URL on the file's lines.

    Run only when ``--check-external`` is set. We dedupe per-line so a
    single URL referenced multiple times on one line still costs one
    HTTP request.
    """
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        seen_on_line: set[str] = set()
        for m in _EXTERNAL_URL_RE.finditer(line):
            url = m.group("url") or m.group("v1") or m.group("v2") or m.group("auto") or ""
            if not url:
                continue
            url = url.rstrip(",.;:").strip()
            if not url or url in seen_on_line:
                continue
            seen_on_line.add(url)
            out.append((lineno, url))
    return out


def _check_one_external_url(
    url: str,
    *,
    timeout: float,
    auth_headers: tuple[tuple[str, str], ...] = (),
    insecure: bool = False,
) -> tuple[int | None, list[int]]:
    """Probe *url* and return ``(final_status, redirect_chain)``.

    * **HEAD-first, GET-fallback** — many CDNs reject HEAD with 403
      while accepting GET. We always GET only when HEAD comes back
      with a status that suggests the HEAD itself was the problem.
    * **Custom auth headers** (``auth_headers`` tuple of ``(name, value)``
      pairs) — supports private URLs like Confluence / Jira /
      Cloudflare-Access-protected pages. Each header is sent verbatim.
    * **Optional cert-validation skip** (``insecure=True``) — falls
      back to a permissive ``ssl.SSLContext`` for self-signed
      internal services. Default off.
    * **Redirect chain capture** — a custom ``HTTPRedirectHandler`` records
      every 30x along the way, returned in the second tuple slot. Lets
      callers diagnose "200 via 3 redirects" patterns that hint at link
      rot. The final status is the destination status (or the last
      redirect status when the chain dead-ends).
    """
    import ssl
    import urllib.error
    import urllib.request

    headers = {
        "User-Agent": ("Mozilla/5.0 (compatible; roam-code-stale-refs/1.0; +https://roam-code.com/)"),
        "Accept": "*/*",
    }
    for name, value in auth_headers:
        headers[name] = value

    chain: list[int] = []

    class _ChainCapturingRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            chain.append(int(code))
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)

    handlers = [_ChainCapturingRedirectHandler()]
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)

    def _try(method: str) -> int | None:
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)
        except (urllib.error.URLError, OSError, ValueError):
            return None

    code = _try("HEAD")
    if code is None or code >= 400:
        # Reset chain so we don't double-count redirects across HEAD + GET.
        chain.clear()
        code_get = _try("GET")
        if code_get is not None:
            return code_get, chain
    return code, chain


_PER_DOMAIN_CONCURRENCY = 2


def _domain_of(url: str) -> str:
    """Return the netloc (host[:port]) of *url*, lowercased; '' on parse failure.

    Used to bucket the per-domain semaphore so repos with many links
    to one origin don't fire all the workers at it simultaneously.
    """
    import urllib.parse

    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def _check_external_urls_parallel(
    urls_with_meta: list[tuple[str, str, int]],
    *,
    timeout: float,
    concurrency: int,
    auth_headers: tuple[tuple[str, str], ...] = (),
    insecure: bool = False,
) -> dict[str, tuple[int | None, list[int]]]:
    """Concurrently HEAD/GET every distinct URL; return ``{url: status}``.

    Two layers of throttle:

    * **Global** ``concurrency`` cap (default 8, max 32) — total
      simultaneous requests across all domains.
    * **Per-domain** semaphore (:data:`_PER_DOMAIN_CONCURRENCY`,
      default 2) — caps simultaneous requests against any one host so
      doc-heavy repos with 100 links to a single origin don't trigger
      anti-bot blocks or rate limits.

    Per-URL dedup happens here so a URL referenced 50 times costs one
    request, not 50.
    """
    import threading
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor, as_completed

    distinct = sorted({u for u, _, _ in urls_with_meta})
    if not distinct:
        return {}
    workers = max(1, min(32, concurrency))
    domain_locks: dict[str, threading.Semaphore] = defaultdict(lambda: threading.Semaphore(_PER_DOMAIN_CONCURRENCY))

    def _check_with_domain_lock(u: str) -> tuple[int | None, list[int]]:
        sem = domain_locks[_domain_of(u)]
        with sem:
            return _check_one_external_url(u, timeout=timeout, auth_headers=auth_headers, insecure=insecure)

    results: dict[str, tuple[int | None, list[int]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_url = {pool.submit(_check_with_domain_lock, u): u for u in distinct}
        for fut in as_completed(future_to_url):
            url = future_to_url[fut]
            try:
                results[url] = fut.result()
            except Exception:
                results[url] = (None, [])
    return results


def _is_external_finding(status: int | None) -> bool:
    """Return True when a status code should surface as a stale-ref finding.

    * ``None`` (timeout / DNS / connection error) → finding (link broken).
    * 4xx / 5xx → finding (link unreachable in a documented way).
    * 2xx / 3xx → live, no finding.
    """
    if status is None:
        return True
    return status >= 400


_EXTERNAL_CACHE_FILENAME = "external-check-cache.json"


def _load_external_cache(project_root: Path, ttl_seconds: float) -> dict[str, int]:
    """Load the URL → status cache, expiring entries older than *ttl_seconds*.

    Cache shape on disk::

        {
            "schema": "roam-stale-refs-external-cache-v1",
            "checked_at": <unix epoch float>,
            "results": {"https://...": <status_code_or_null>, ...}
        }

    Returns the live (non-expired) entries as a dict. On any read or
    parse failure returns an empty dict — the caller falls back to
    fresh probes.

    The simple-but-correct shape: one timestamp shared across all
    cache entries written in the same scan. When expired, the whole
    file is treated as stale. Per-URL TTLs would over-engineer this.
    """
    if ttl_seconds <= 0:
        return {}
    path = project_root / ".roam" / _EXTERNAL_CACHE_FILENAME
    if not path.exists():
        return {}
    try:
        import json as _json

        with open(path, encoding="utf-8") as fh:
            payload = _json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    checked_at = payload.get("checked_at", 0)
    if not isinstance(checked_at, (int, float)):
        return {}
    if (time.time() - float(checked_at)) > ttl_seconds:
        return {}
    results = payload.get("results")
    if not isinstance(results, dict):
        return {}
    out: dict[str, int] = {}
    for url, status in results.items():
        if not isinstance(url, str):
            continue
        if status is None:
            # Cached "unreachable" entries persist too — refreshing
            # too aggressively would obscure flaky-vs-broken signal.
            out[url] = -1
        elif isinstance(status, int):
            out[url] = status
    return out


def _save_external_cache(project_root: Path, results: dict[str, int | None]) -> None:
    """Persist the URL → status results to ``.roam/external-check-cache.json``.

    Best-effort. Fails silently when ``.roam/`` doesn't exist or isn't
    writable — the cache is an optimisation, not a correctness layer.
    """
    import json as _json

    cache_dir = project_root / ".roam"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    serialisable: dict[str, int | None] = {}
    for url, status in results.items():
        if status is None or status == -1:
            serialisable[url] = None
        else:
            serialisable[url] = int(status)
    payload = {
        "schema": "roam-stale-refs-external-cache-v1",
        "checked_at": time.time(),
        "results": serialisable,
    }
    try:
        with open(cache_dir / _EXTERNAL_CACHE_FILENAME, "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, sort_keys=True)
    except OSError as _exc:
        # A cache-write failure forces the next run to re-check every
        # external URL — surface lineage so the lost cache has a cause.
        from roam.observability import log_swallowed

        log_swallowed("cmd_stale_refs:write_external_cache", _exc)


def _scan_external_urls(
    project_root: Path,
    *,
    include_excluded: bool,
    ignore_source: tuple[str, ...],
    timeout: float,
    concurrency: int,
    cache_ttl: float = 0.0,
    allow_status: tuple[int, ...] = (),
    auth_headers: tuple[tuple[str, str], ...] = (),
    insecure: bool = False,
) -> tuple[list[dict], dict[str, int]]:
    """Walk the repo, extract external URLs, check them, return broken ones.

    Returns ``(findings, cache_meta)`` where ``cache_meta`` is::

        {"hits": int, "misses": int, "checked_total": int}

    so the caller can surface per-scan cache stats. When *cache_ttl* > 0,
    we consult ``.roam/external-check-cache.json`` to skip URLs probed
    within the TTL window — repeat CI scans pay HEAD/GET cost only on
    URLs that haven't been seen recently.
    """
    all_files = discover_files(project_root, include_excluded=include_excluded)
    urls_with_meta: list[tuple[str, str, int]] = []
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
        for lineno, url in _extract_external_urls(content):
            urls_with_meta.append((url, rel, lineno))

    cache_meta = {"hits": 0, "misses": 0, "checked_total": 0}
    if not urls_with_meta:
        return [], cache_meta

    cached = _load_external_cache(project_root, cache_ttl)
    distinct_urls = sorted({u for u, _, _ in urls_with_meta})
    cache_meta["checked_total"] = len(distinct_urls)
    cache_meta["hits"] = sum(1 for u in distinct_urls if u in cached)

    # Build the to-probe list: only URLs not in cache.
    to_probe = [(u, src, line) for u, src, line in urls_with_meta if u not in cached]
    cache_meta["misses"] = len({u for u, _, _ in to_probe})

    fresh_results: dict[str, tuple[int | None, list[int]]] = {}
    if to_probe:
        fresh_results = _check_external_urls_parallel(
            to_probe,
            timeout=timeout,
            concurrency=concurrency,
            auth_headers=auth_headers,
            insecure=insecure,
        )

    # Merge cached + fresh into one status map. Cache stores statuses
    # only (no chain), so cached entries get an empty chain on read —
    # losing redirect-chain detail in exchange for not invalidating
    # the cache when the chain capture changed.
    statuses: dict[str, int | None] = {}
    chains: dict[str, list[int]] = {}
    for url in distinct_urls:
        if url in cached:
            value = cached[url]
            statuses[url] = None if value == -1 else value
            chains[url] = []
        else:
            fresh = fresh_results.get(url, (None, []))
            statuses[url] = fresh[0]
            chains[url] = fresh[1]
    if cache_ttl > 0 and to_probe:
        # Persist ONLY if we actually probed something new — preserves
        # the existing cache's checked_at timestamp otherwise. We merge
        # cached + fresh so the saved file represents the union.
        merged: dict[str, int | None] = {}
        for url in distinct_urls:
            merged[url] = statuses[url]
        _save_external_cache(project_root, merged)

    allow_set = set(allow_status)

    findings: list[dict] = []
    for url, source_file, lineno in urls_with_meta:
        status = statuses.get(url)
        # Allow-list takes precedence: user-listed status codes count
        # as "live" even though they'd otherwise be findings.
        if status is not None and status in allow_set:
            continue
        if not _is_external_finding(status):
            continue
        finding: dict = {
            "file": source_file,
            "line": lineno,
            "kind": "external",
            "raw": url,
            "status": status if status is not None else "unreachable",
        }
        chain = chains.get(url) or []
        if chain:
            finding["redirect_chain"] = chain
        findings.append(finding)
    return findings, cache_meta


# ---------------------------------------------------------------------------
# Persistent baseline
# ---------------------------------------------------------------------------


_BASELINE_SCHEMA = "roam-stale-refs-baseline-v2"


def _baseline_record_for(target: str, source: dict) -> str:
    """Stable line-tolerant identity for a finding.

    Format: ``"<target>|<file>:<kind>"`` — line numbers deliberately
    excluded so baselined findings survive cosmetic text shifts (a
    user adding a copyright header that pushes every line down by 5).
    The pipe is unlikely to appear in any real path or kind name.

    Trade-off: a file with two refs to the same target shows as ONE
    baseline record. If only one of them gets fixed (the URL is
    rewritten in place), the still-broken one stays baselined — which
    is correct, since the user's "I acknowledge this debt" decision
    extended to the (target, file, kind) tuple. New findings (different
    target, different file, or different kind) still surface for the
    gate.
    """
    return f"{target}|{source['file']}:{source['kind']}"


def _save_baseline(stale_by_target: dict[str, list[dict]], path: str) -> None:
    """Write a deterministic JSON snapshot of every finding to *path*.

    Schema::

        {
            "schema": "roam-stale-refs-baseline-v2",
            "saved_at": "<UTC ISO>",
            "finding_count": N,
            "findings": ["<target>|<file>:<kind>", ...]
        }

    Records are deduplicated and sorted so subsequent saves on the same
    scan produce identical files (good for git diff-ability) and
    multiple sources of the same (target, file, kind) collapse to one
    entry.

    The schema name is bumped to v2 because line numbers are no longer
    encoded — see :func:`_baseline_record_for`. v1 baselines (which
    include line numbers) are still accepted by :func:`_load_baseline`
    via post-load normalisation.
    """
    import json
    from datetime import datetime, timezone

    records: set[str] = set()
    for target, sources in stale_by_target.items():
        for s in sources:
            records.add(_baseline_record_for(target, s))
    record_list = sorted(records)
    payload = {
        "schema": _BASELINE_SCHEMA,
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finding_count": len(record_list),
        "findings": record_list,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _normalise_baseline_record(record: str) -> str:
    """Translate a v1 record (with line) into the v2 line-less form.

    v1: ``<target>|<file>:<line>:<kind>``
    v2: ``<target>|<file>:<kind>``

    We split off everything before the first ``|`` (the target — may
    contain colons in URLs / anchors), then look at the remainder. If
    the post-pipe portion has 3 colon-separated parts (file:line:kind),
    we drop the middle part. If it has 2 (already v2), we keep it.

    Records we can't classify are returned unchanged so a future schema
    we don't recognise still flows through cleanly.
    """
    if "|" not in record:
        return record
    target, _, rest = record.partition("|")
    parts = rest.split(":")
    if len(parts) == 3:
        # v1 → v2: drop the middle (line) part.
        return f"{target}|{parts[0]}:{parts[2]}"
    return record


def _load_baseline(path: str) -> set[str]:
    """Load baseline records into a set for fast membership checks.

    Accepts both v1 (line-precise) and v2 (line-tolerant) baselines —
    v1 records are normalised on read so they match v2 records produced
    by the current scanner. Returns an empty set on any read or schema
    error so the caller can proceed without the baseline filter rather
    than crash.
    """
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return set()
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list):
        return set()
    return {_normalise_baseline_record(str(r)) for r in findings if isinstance(r, str)}


def _filter_against_baseline(
    stale_by_target: dict[str, list[dict]],
    baseline: set[str],
) -> dict[str, list[dict]]:
    """Drop sources matching the baseline; keep targets that retain >0 sources."""
    if not baseline:
        return stale_by_target
    out: dict[str, list[dict]] = {}
    for target, sources in stale_by_target.items():
        kept = [s for s in sources if _baseline_record_for(target, s) not in baseline]
        if kept:
            out[target] = kept
    return out


# ---------------------------------------------------------------------------
# Repo config (.roam/stale-refs.toml)
# ---------------------------------------------------------------------------


_CONFIG_FILENAME = "stale-refs.toml"


def _load_repo_config(project_root: Path) -> dict:
    """Load ``.roam/stale-refs.toml`` if present; return ``{}`` otherwise.

    Schema is intentionally flat — keys mirror CLI flag names with
    underscores instead of dashes:

    .. code-block:: toml

        ignore = ["CHANGELOG.md", "docs/legacy/**"]
        ignore_target = ["AGENTS.md"]
        sort_by = "priority"
        check_anchors = true
        check_external = false
        # external_allow_status = [401, 403]
        # external_auth_header = ["Authorization: Bearer $TOKEN"]

    CLI flags always override config values — config is for repo
    defaults. Loaded by the Click command at the start of each run;
    individual flags can be made config-aware by checking ``ctx.obj``.

    Uses Python 3.11+ stdlib ``tomllib`` when available; falls back to
    a minimal parser on 3.10 (the project's current floor).
    """
    config_path = project_root / ".roam" / _CONFIG_FILENAME
    if not config_path.exists():
        return {}
    try:
        try:
            import tomllib  # type: ignore[import-not-found]

            with open(config_path, "rb") as fh:
                return tomllib.load(fh)
        except ImportError:
            # Python < 3.11 — fall back to ``tomli`` if available; if
            # neither is installed, parse a minimal subset by hand. We
            # don't add a hard dep just for this config file.
            try:
                import tomli  # type: ignore[import-not-found]

                with open(config_path, "rb") as fh:
                    return tomli.load(fh)
            except ImportError:
                return _parse_minimal_toml(config_path)
    except (OSError, ValueError):
        return {}


_TOML_SENTINEL_SKIP = object()


def _parse_toml_scalar(tok: str, *, on_unknown: object = _TOML_SENTINEL_SKIP) -> object:
    """Parse a single TOML scalar token (string / bool / int / float).

    ``on_unknown`` controls the fallback for unrecognized tokens:
    - ``_TOML_SENTINEL_SKIP`` (default) returns the sentinel so the caller can
      drop the entry — matches the top-level scalar behavior of the legacy
      parser, which silently skipped unparseable values.
    - any other value (e.g. ``tok`` itself) is returned as-is — matches the
      array-item behavior, which kept the raw token rather than dropping it.

    Surgical refactor: replaces two nearly-identical 4-deep try/except ladders
    in ``_parse_minimal_toml`` (W: complexity 162 → ~30, nest 8 → 4).
    """
    if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
        return tok[1:-1]
    if tok == "true":
        return True
    if tok == "false":
        return False
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return on_unknown


def _parse_minimal_toml(path: Path) -> dict:
    """Last-resort TOML parser for config files we wrote ourselves.

    Handles only the shapes ``init`` emits: top-level scalar/string
    keys, scalar/string lists, booleans. No tables, no arrays of
    tables, no inline arrays of dicts. Anything we can't parse is
    silently skipped so a future TOML feature in the file doesn't
    break old CLIs.
    """
    out: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().rstrip(",")
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                out[key] = []
                continue
            items: list = []
            for tok in _split_toml_array(inner):
                tok = tok.strip().rstrip(",").strip()
                if not tok:
                    continue
                # Array items: keep raw token on unparseable (legacy behavior).
                items.append(_parse_toml_scalar(tok, on_unknown=tok))
            out[key] = items
        else:
            # Top-level scalars: silently skip unparseable (legacy behavior).
            parsed = _parse_toml_scalar(value)
            if parsed is not _TOML_SENTINEL_SKIP:
                out[key] = parsed
    return out


def _split_toml_array(inner: str) -> list[str]:
    """Split an inline TOML array body, respecting quoted-string commas."""
    parts: list[str] = []
    buf: list[str] = []
    in_string = False
    for ch in inner:
        if ch == '"':
            in_string = not in_string
            buf.append(ch)
        elif ch == "," and not in_string:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _render_github_step_summary(
    *,
    verdict: str,
    targets: list[dict],
    files_scanned: int,
    refs_seen: int,
) -> str:
    """Render a GitHub-flavoured Actions step summary.

    Single H2 + a table: target / confidence / hint / source files. The
    ``$GITHUB_STEP_SUMMARY`` file accepts markdown verbatim and renders
    inline on the workflow run page. We cap rows at 50 to stay under
    GitHub's 1MB summary limit even on a doc-thrashed PR.
    """
    lines: list[str] = []
    lines.append("## roam stale-refs")
    lines.append("")
    lines.append(f"**Verdict:** {verdict}")
    lines.append("")
    lines.append(f"_Scanned {files_scanned} files, checked {refs_seen} references._")
    lines.append("")
    if not targets:
        lines.append("All references resolve. No findings.")
        return "\n".join(lines) + "\n"
    lines.append("| Missing target | Confidence | Hint | Sources |")
    lines.append("|---|---|---|---|")
    for tgt in targets[:50]:
        hint = tgt.get("hint") or {}
        conf = hint.get("confidence", "—")
        hint_str = hint.get("target", "—")
        sources = tgt.get("sources") or []
        # Show up to 3 source-file refs, comma-separated.
        src_str = ", ".join(f"`{s.get('file', '?')}`:{s.get('line', '?')}" for s in sources[:3])
        if len(sources) > 3:
            src_str += f" (+{len(sources) - 3})"
        lines.append(f"| `{tgt['target']}` | {conf} | {hint_str} | {src_str} |")
    if len(targets) > 50:
        lines.append("")
        lines.append(f"_…and {len(targets) - 50} more (truncated)._")
    return "\n".join(lines) + "\n"


def _log_hint_acceptances(
    project_root: Path,
    targets: list[dict],
    edits_by_source: dict,
) -> None:
    """Append accepted-hint provenance rows to ``.roam/hint-acceptances.jsonl``.

    Captured per acceptance: timestamp, source provider, source confidence,
    rewritten URL → new URL, source file path. Append-only JSONL so a
    future smarter ranker can mine the file for "this provider's
    HIGH-confidence hints get accepted N% of the time" stats.

    Best-effort. We never crash the scan because telemetry hit a
    PermissionError — log dirs on Windows can be locked by a parallel
    process and that's fine.
    """
    try:
        edited_paths = set(edits_by_source.keys())
        if not edited_paths:
            return
        log_dir = project_root / ".roam"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "hint-acceptances.jsonl"
        ts = int(time.time())
        rows: list[str] = []
        for tgt in targets:
            hint = tgt.get("hint")
            if not hint or hint.get("confidence") not in {"HIGH", "MEDIUM"}:
                continue
            sources = tgt.get("sources") or []
            for s in sources:
                src_file = s.get("file")
                if src_file in edited_paths:
                    rows.append(
                        json.dumps(
                            {
                                "ts": ts,
                                "missing": tgt["target"],
                                "rewrite": hint["target"],
                                "confidence": hint["confidence"],
                                "source": hint.get("source", "unknown"),
                                "src_file": src_file,
                                "src_line": s.get("line"),
                                "kind": s.get("kind"),
                            },
                            separators=(",", ":"),
                        )
                    )
        if rows:
            with open(log_path, "a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(row + "\n")
    except OSError:
        return


# Predicate-type constants for the in-toto attestation. The producer
# always emits ``_CURRENT_STALE_REFS_PREDICATE_TYPE`` (the .com URI). The
# verifier accepts any IRI in ``_ACCEPTED_STALE_REFS_PREDICATE_TYPES`` so
# attestations produced before the CGA migration to .com keep verifying.
_CURRENT_STALE_REFS_PREDICATE_TYPE = "https://roam-code.com/StaleRefs/v1"
_LEGACY_STALE_REFS_PREDICATE_TYPES = (
    "https://roam-code.dev/StaleRefs/v1",  # legacy: pre-CGA-migration emissions
)
_ACCEPTED_STALE_REFS_PREDICATE_TYPES = (
    _CURRENT_STALE_REFS_PREDICATE_TYPE,
    *_LEGACY_STALE_REFS_PREDICATE_TYPES,
)


def build_stale_refs_attestation(
    *,
    project_root: Path,
    summary: dict,
    targets: list[dict],
    findings: list[dict],
) -> dict:
    """Build an in-toto v1 Statement wrapping a stale-refs scan.

    Predicate type is ``https://roam-code.com/StaleRefs/v1`` — distinct
    from the CGA / CGA-AIBOM predicates so verifiers can tell the
    artifacts apart. Subject is the git commit SHA so the attestation
    binds to a specific repo state.

    Returned statement is unsigned. Sign it via
    ``roam.attest.cga.cosign_sign_statement`` or pipe it through cosign
    out-of-band. Verifying side calls
    ``verify_stale_refs_attestation`` (see below).

    Predicate fields (the things auditors care about):

    * ``scan_summary`` — verbatim copy of the verdict-bearing summary
      block (verdict / counts / by_confidence / by_kind etc).
    * ``targets`` — full per-target list with deterministic ordering.
    * ``findings_count`` — total raw finding rows scanned.
    * ``tool`` — name + version + git SHA so the artifact is reproducible.

    The shape mirrors the CGA flow: deterministic JSON serialisation
    (sort_keys=True, no whitespace) so digest stability is guaranteed
    across runs on the same repo state.
    """
    from roam.attest.cga import (
        STATEMENT_TYPE,
        _detect_tool_version,
        _git_commit_sha,
        _git_remote_url,
    )

    sha = _git_commit_sha(project_root) or "unknown"
    remote = _git_remote_url(project_root)
    subject_name = remote or str(project_root.resolve()).replace("\\", "/")
    sorted_targets = sorted(
        ({k: v for k, v in t.items() if k != "rename_hint"} for t in targets),
        key=lambda t: t.get("target", ""),
    )
    predicate = {
        "scan_summary": dict(summary),
        "targets": sorted_targets,
        "findings_count": len(findings),
        "tool": {
            "name": "roam-stale-refs",
            "version": _detect_tool_version(),
            "predicate_type": _CURRENT_STALE_REFS_PREDICATE_TYPE,
        },
    }
    return {
        "_type": STATEMENT_TYPE,
        "predicateType": _CURRENT_STALE_REFS_PREDICATE_TYPE,
        "subject": [{"name": subject_name, "digest": {"git_commit_sha1": sha}}],
        "predicate": predicate,
    }


def verify_stale_refs_attestation(
    statement: dict,
    *,
    expected_commit_sha: str | None = None,
) -> tuple[bool, str]:
    """Lightweight predicate-shape check on a stale-refs in-toto statement.

    Returns ``(True, "")`` on success, ``(False, reason)`` otherwise.
    Verifying signatures is delegated to ``cosign verify-blob`` (out
    of band) — this function only validates *structure*.
    """
    if not isinstance(statement, dict):
        return False, "statement is not an object"
    if statement.get("predicateType") not in _ACCEPTED_STALE_REFS_PREDICATE_TYPES:
        return False, "wrong predicateType"
    subject = statement.get("subject") or []
    if not isinstance(subject, list) or len(subject) != 1:
        return False, "subject must be a single-element list"
    predicate = statement.get("predicate") or {}
    if "scan_summary" not in predicate:
        return False, "missing scan_summary"
    if "targets" not in predicate:
        return False, "missing targets"
    if expected_commit_sha:
        digest = (subject[0] or {}).get("digest", {})
        if digest.get("git_commit_sha1") != expected_commit_sha:
            return False, "subject SHA does not match expected commit"
    return True, ""


def _suggest_config_for_repo(project_root: Path) -> dict:
    """Walk the repo and propose sensible default ``.roam/stale-refs.toml`` content.

    Heuristics (kept conservative — better to under-suggest than over-suggest):

    * Always include ``CHANGELOG.md`` in ``ignore`` if it exists —
      historical entries reference deleted files routinely.
    * Add ``docs/legacy/**`` if such a directory exists.
    * Add ``ignore_target`` entries for common documentation
      placeholders (``AGENTS.md``, ``GEMINI.md``, ``CLAUDE.md``,
      ``CONVENTIONS.md``) that a doc site might mention without
      requiring them to exist.
    * Default ``sort_by = "priority"`` — most useful for triage.

    Returns the dict shape; callers serialise it as TOML.
    """
    suggestions: dict = {"sort_by": "priority"}
    ignore: list[str] = []
    if (project_root / "CHANGELOG.md").exists():
        ignore.append("CHANGELOG.md")
    if (project_root / "docs" / "legacy").is_dir():
        ignore.append("docs/legacy/**")
    if ignore:
        suggestions["ignore"] = ignore
    suggested_targets: list[str] = []
    for placeholder in ("AGENTS.md", "GEMINI.md", "CLAUDE.md", "CONVENTIONS.md"):
        if not (project_root / placeholder).exists():
            suggested_targets.append(placeholder)
    if suggested_targets:
        suggestions["ignore_target"] = suggested_targets
    return suggestions


def _serialise_config_toml(config: dict) -> str:
    """Write the dict as TOML text; safe for our suggested key shape."""
    lines: list[str] = []
    lines.append("# .roam/stale-refs.toml — repo-level defaults for `roam stale-refs`.")
    lines.append("# CLI flags override these values. Generated by `roam stale-refs init`.")
    lines.append("")
    for key, value in config.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")
        elif isinstance(value, str):
            escaped = value.replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        elif isinstance(value, list):
            if not value:
                lines.append(f"{key} = []")
            else:
                inner = ", ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in value)
                lines.append(f"{key} = [{inner}]")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# --watch mode
# ---------------------------------------------------------------------------


def _scan_finding_set(stale_by_target: dict[str, list[dict]]) -> set[tuple[str, str, int, str]]:
    """Flatten findings into a set of ``(target, source_file, line, kind)``.

    Used by ``--watch`` to diff between consecutive scans. The tuple is
    intentionally identity-bearing (target + location + kind) so that
    rewriting the same line to a different target shows as one resolved
    + one new, not as a "modified" finding.
    """
    out: set[tuple[str, str, int, str]] = set()
    for target, sources in stale_by_target.items():
        for s in sources:
            out.add((target, s["file"], int(s["line"]), s["kind"]))
    return out


def _collect_mtimes(project_root: Path, *, include_excluded: bool) -> dict[str, float]:
    """Return ``{rel_path: mtime}`` for every scannable repo file.

    Watch mode uses this signature to detect changes before doing the
    expensive rescan. Files that fail to ``stat`` are simply absent from
    the map — they'll trigger "missing" or "added" deltas naturally.

    Implementation: ``discover_files`` enumerates the candidates;
    ``os.scandir`` then batches ``stat`` info per parent directory in a
    single syscall instead of one ``Path.stat`` per file. On a 10K-file
    repo this cuts ~80% of the per-poll overhead.
    """
    out: dict[str, float] = {}
    try:
        all_files = discover_files(project_root, include_excluded=include_excluded)
    except Exception:
        return out

    # Bucket files by their parent dir so each scandir() call returns
    # mtimes for many files at once. The ``DirEntry.stat`` returned by
    # scandir is cached on Linux/macOS and only costs one extra syscall
    # on Windows — still strictly faster than Path.stat per-file.
    by_parent: dict[str, list[str]] = defaultdict(list)
    for rel in all_files:
        ext = os.path.splitext(rel)[1].lower()
        if ext not in _SCANNABLE_EXTS:
            continue
        norm = rel.replace("\\", "/")
        parent = norm.rsplit("/", 1)[0] if "/" in norm else ""
        by_parent[parent].append(norm)

    for parent, rels in by_parent.items():
        wanted = set(rels)
        scan_dir = project_root if parent == "" else project_root / parent
        try:
            with os.scandir(scan_dir) as it:
                for entry in it:
                    rel_norm = entry.name if parent == "" else f"{parent}/{entry.name}"
                    if rel_norm not in wanted:
                        continue
                    try:
                        out[rel_norm] = entry.stat().st_mtime
                    except OSError:
                        continue
        except OSError:
            # Directory disappeared between discover_files and scandir
            # (rare race). Just skip; the next poll will catch up.
            continue
    return out


def _format_finding_line(target: str, source_file: str, line: int, kind: str) -> str:
    """Single-line representation used inside the watch loop's delta output."""
    return f"  {source_file}:{line}  [{kind}]  → {target}"


def _run_watch_loop(
    project_root: Path,
    *,
    interval: float,
    scan_kwargs: dict,
    on_initial: callable,
    on_delta: callable,
    baseline: set[str] | None = None,
) -> None:
    """Drive the ``--watch`` polling loop until interrupted.

    Strategy:

    * Run the initial scan once and call ``on_initial(stale_by_target,
      meta)`` so the caller can render the welcome banner.
    * Snapshot mtimes for every scannable file.
    * Sleep ``interval`` seconds; recheck mtimes. If anything changed,
      sleep an additional ~30% of ``interval`` to debounce flurries
      (editors that rapid-rewrite on save), then rescan and call
      ``on_delta(added, resolved, total)``.
    * On KeyboardInterrupt, return cleanly so the Click command exits
      with code 0.

    When *baseline* is provided (from a ``--baseline-from`` file at the
    Click command level), it's applied to every scan — initial + each
    polled rescan — so the watch deltas reflect only the
    non-baselined finding set. Without this, a user with 100 baselined
    findings would see them all appear in the initial banner and
    re-flicker on every cycle, defeating the purpose of the baseline.

    All scan work happens inline on the main thread — no background
    threads, no signals — because the polling cadence is slow enough
    that a blocking rescan is acceptable and keeping the event loop
    serial avoids debugging surprises.
    """
    include_excluded = scan_kwargs.get("include_excluded", False)
    stale_by_target, files_scanned, refs_seen, basename_idx, anchor_cache = _scan_project(project_root, **scan_kwargs)
    if baseline:
        stale_by_target = _filter_against_baseline(stale_by_target, baseline)
    on_initial(stale_by_target, files_scanned, refs_seen)
    last_findings = _scan_finding_set(stale_by_target)
    last_mtimes = _collect_mtimes(project_root, include_excluded=include_excluded)

    try:
        while True:
            time.sleep(max(0.1, interval))
            current_mtimes = _collect_mtimes(project_root, include_excluded=include_excluded)
            if current_mtimes == last_mtimes:
                continue
            # Debounce: editor saves often produce 2-3 rapid mtime
            # bumps in a row. Sleep ~30% of interval before rescanning.
            time.sleep(max(0.05, interval * 0.3))
            last_mtimes = _collect_mtimes(project_root, include_excluded=include_excluded)

            stale_by_target, files_scanned, refs_seen, basename_idx, anchor_cache = _scan_project(
                project_root, **scan_kwargs
            )
            if baseline:
                stale_by_target = _filter_against_baseline(stale_by_target, baseline)
            current_findings = _scan_finding_set(stale_by_target)
            added = current_findings - last_findings
            resolved = last_findings - current_findings
            on_delta(added, resolved, current_findings)
            last_findings = current_findings
    except KeyboardInterrupt:
        return


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


@roam_capability(
    name="stale-refs",
    category="refactoring",
    summary="Find dangling file references — markdown links, HTML href/src, backtick paths",
    maturity="stable",
    mcp_expose=True,
    mcp_preset=("core",),
    side_effect=True,
    task_required=False,
    destructive=False,
    stale_sensitive=True,
    ai_safe=True,
    requires_index=True,
)
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
    "--with-candidates",
    is_flag=True,
    default=False,
    help=(
        "Include a sample of repo file paths in the JSON envelope under "
        "``summary.repo_paths_sample``. Used by the MCP wrapper's "
        "LLM-enrichment path so the calling agent's model can suggest "
        "semantic matches for findings the deterministic providers miss."
    ),
)
@click.option(
    "--watch",
    is_flag=True,
    default=False,
    help=(
        "Continuous mode: run an initial scan, then poll the repo for "
        "file changes and rescan. Prints only newly-introduced and "
        "newly-resolved findings on each cycle so the terminal stays "
        "useful during a refactoring session. Ctrl+C to exit."
    ),
)
@click.option(
    "--watch-interval",
    type=float,
    default=1.5,
    show_default=True,
    help="Seconds between watch-mode polls. Lower = more responsive, more CPU.",
)
@click.option(
    "--baseline-save",
    "baseline_save",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help=(
        "Save the current findings to FILE as a JSON baseline. Use it "
        "later with ``--baseline-from FILE`` to filter scans down to "
        "newly-introduced findings (unlike ``--diff`` which is "
        "git-based, the baseline is a frozen snapshot — useful when "
        "your team has agreed to acknowledge a chunk of legacy "
        "findings and only wants to gate on regressions). "
        "Composing ``--baseline-from a.json --baseline-save b.json`` "
        "saves the POST-filter findings — useful for incremental "
        'rebaselining ("forget the old debt I already acknowledged '
        "and freeze the new findings I'm choosing to live with too\")."
    ),
)
@click.option(
    "--baseline-from",
    "baseline_from",
    type=click.Path(dir_okay=False, exists=True, readable=True),
    default=None,
    help=(
        "Filter findings to only those NOT in the saved baseline FILE. "
        "Composes with ``--gate`` so CI fails only on newly-introduced "
        "findings while pre-existing ones stay visible but ignored."
    ),
)
@click.option(
    "--check-external",
    is_flag=True,
    default=False,
    help=(
        "Also check ``http(s)://`` URLs via HEAD requests. Opt-in "
        "(off by default to keep the scan fully local + offline). "
        "Findings surface with ``kind=external``. Concurrent + cached "
        "per scan."
    ),
)
@click.option(
    "--external-timeout",
    type=float,
    default=5.0,
    show_default=True,
    help="Per-URL HTTP timeout in seconds (used only with --check-external).",
)
@click.option(
    "--external-concurrency",
    type=int,
    default=8,
    show_default=True,
    help="Parallel HTTP requests during --check-external (1-32).",
)
@click.option(
    "--external-cache-ttl",
    type=float,
    default=86400.0,
    show_default=True,
    help=(
        "Cache external check results in ``.roam/external-check-cache.json`` "
        "for this many seconds. Default 24h. Pass 0 to disable caching "
        "(re-probe every run). CI scans benefit massively from caching; "
        "interactive runs may want fresher data."
    ),
)
@click.option(
    "--external-allow-status",
    "external_allow_status",
    multiple=True,
    metavar="CODE",
    help=(
        "HTTP status codes to treat as live (not findings). Repeatable. "
        "Use case: internal docs link to auth-gated services that 401/403 "
        "but are healthy. ``--external-allow-status 401 --external-allow-status 403`` "
        "or comma-separated ``--external-allow-status 401,403``."
    ),
)
@click.option(
    "--external-auth-header",
    "external_auth_header",
    multiple=True,
    metavar='"NAME: VALUE"',
    help=(
        "Custom HTTP header sent with --check-external probes. Repeatable. "
        'Format: ``"Authorization: Bearer ${TOKEN}"``. Useful for '
        "private dashboards or internal-network probes."
    ),
)
@click.option(
    "--external-insecure",
    is_flag=True,
    default=False,
    help=(
        "Skip TLS cert verification on --check-external. For self-signed "
        "internal services. Default off (secure) — only enable when you "
        "know the URLs you're probing."
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
        "Auto-rename HIGH-confidence findings. ``preview`` prints a "
        "unified diff and exits 0; ``apply`` rewrites files in place via "
        "atomic tempfile + rename (so an interrupted run cannot corrupt "
        "source files). Only edits lines with a single unambiguous "
        "stale reference. Safe with uncommitted changes — substitution "
        "is per-raw-URL and skips silently if the URL no longer appears "
        "verbatim — but reviewing the diff via ``--fix preview`` first "
        "is the recommended workflow."
    ),
)
@click.option(
    "--fix-medium",
    is_flag=True,
    default=False,
    help=(
        "When combined with ``--fix preview/apply``, also act on "
        "MEDIUM-confidence hints (single-basename-match-elsewhere, "
        "anchor-similarity ≥ 0.6). Off by default — MEDIUM hints are "
        "advisory and shouldn't auto-rewrite without an explicit opt-in. "
        "Useful for one-shot post-refactor cleanups where the user trusts "
        "the suggestions and will review the diff via git afterwards."
    ),
)
@click.option(
    "--scan-bare-backticks",
    is_flag=True,
    default=False,
    help=(
        "Treat bare backtick-wrapped strings in prose (e.g. "
        "`` `docs/foo.md` ``) as filesystem references. Off by default "
        "because backticks are inline code, not link syntax — on real "
        "repos this produces ~39% false positives (prose mentions of "
        "filenames that aren't claims about disk layout). Opt in when "
        "you specifically want to audit narrative filename mentions, "
        "e.g. after a large rename refactor. Hyperlinked references "
        "(``[`x`](url)``) always use the URL half regardless of this flag."
    ),
)
@click.option(
    "--init",
    is_flag=True,
    default=False,
    help=(
        "Generate ``.roam/stale-refs.toml`` with sensible defaults for "
        "this repo (ignores CHANGELOG.md, common doc placeholders, etc) "
        "and exit. Subsequent `roam stale-refs` runs read the file as "
        "default config — CLI flags still override. Use --init-force to "
        "overwrite an existing file."
    ),
)
@click.option(
    "--init-force",
    is_flag=True,
    default=False,
    help="Overwrite an existing .roam/stale-refs.toml when running --init.",
)
@click.option(
    "--attest",
    "attest_path",
    default=None,
    metavar="PATH",
    help=(
        "Write an in-toto v1 attestation of the scan results to PATH "
        "(JSON, predicateType ``https://roam-code.com/StaleRefs/v1``). "
        "Sign out-of-band with cosign for tamper-evident provenance. "
        "Pass ``-`` to write to stdout instead of a file."
    ),
)
@click.option(
    "--root",
    "root_override",
    default=None,
    metavar="PATH",
    help=(
        "Scan a different repo / monorepo subtree from the current "
        "working directory. PATH is treated as the project root, "
        "overriding the auto-detected git root. Useful for monorepos "
        "where each top-level dir is a sub-project."
    ),
)
@click.option(
    "--github-summary",
    "github_summary_path",
    default=None,
    metavar="PATH",
    help=(
        "Write a GitHub-flavoured markdown summary table to PATH "
        "(use ``$GITHUB_STEP_SUMMARY`` from inside Actions). One "
        "row per missing target, with confidence / hint / source "
        "file links. Renders inline on the workflow run page."
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
    with_candidates,
    watch,
    watch_interval,
    baseline_save,
    baseline_from,
    check_external,
    external_timeout,
    external_concurrency,
    external_cache_ttl,
    external_allow_status,
    external_auth_header,
    external_insecure,
    no_anchors,
    diff_base,
    sort_by,
    fix,
    fix_medium,
    scan_bare_backticks,
    init,
    init_force,
    attest_path,
    root_override,
    github_summary_path,
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
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    if root_override:
        # ``--root foo/`` — treat the supplied path as the project
        # root rather than auto-detecting the surrounding git repo.
        # Required for monorepo workflows where each subtree is its
        # own scan, and for CI runs where the repo doesn't have a git
        # working tree (sparse checkouts, archive scans).
        project_root = Path(root_override).resolve()
        if not project_root.is_dir():
            raise click.UsageError(f"--root path {root_override!r} is not a directory")
    else:
        project_root = find_project_root()

    # ---- ``--init`` short-circuits everything ---------------------
    if init:
        suggested = _suggest_config_for_repo(project_root)
        toml_text = _serialise_config_toml(suggested)
        config_path = project_root / ".roam" / _CONFIG_FILENAME
        if config_path.exists() and not init_force:
            click.echo(f"VERDICT: {config_path} already exists. Pass --init-force to overwrite, or hand-edit the file.")
            return
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(toml_text, encoding="utf-8")
        click.echo(f"VERDICT: wrote {config_path}\n")
        click.echo("Suggested config:\n")
        click.echo(toml_text.rstrip())
        click.echo()
        click.echo("Edit the file to fit your repo, then run `roam stale-refs` normally.")
        return

    # ---- Repo config (loaded once, CLI flags still override) ------
    repo_config = _load_repo_config(project_root)
    if repo_config:
        # Each config key is folded into the matching CLI param ONLY when
        # the user didn't explicitly pass that flag. We detect "unset"
        # using empty tuples / default values; not perfect but covers
        # the common cases without a Click introspection ladder.
        if not ignore_source and isinstance(repo_config.get("ignore"), list):
            ignore_source = tuple(p for p in repo_config["ignore"] if isinstance(p, str))
        if not ignore_target and isinstance(repo_config.get("ignore_target"), list):
            ignore_target = tuple(p for p in repo_config["ignore_target"] if isinstance(p, str))
        # Phase 4B fix: ``sort_by``, ``check_external``, ``no_anchors``,
        # and ``limit`` were silently dropped by the v12.49 first-pass
        # loader — the suggested config file even wrote ``sort_by =
        # "priority"`` and the runtime ignored it. Wire them in so the
        # config file is the single source of repo-level defaults.
        if sort_by == "priority" and isinstance(repo_config.get("sort_by"), str):
            cfg_sort = repo_config["sort_by"]
            if cfg_sort in {"priority", "ref-count", "alpha"}:
                sort_by = cfg_sort
        if not check_external and isinstance(repo_config.get("check_external"), bool):
            check_external = repo_config["check_external"]
        if not no_anchors and repo_config.get("check_anchors") is False:
            # Inverted polarity: TOML reads better as ``check_anchors =
            # false`` than ``no_anchors = true`` for the operator.
            no_anchors = True
        if limit == 20 and isinstance(repo_config.get("limit"), int):
            cfg_limit = repo_config["limit"]
            if 1 <= cfg_limit <= 1000:
                limit = cfg_limit

    # ---- Watch mode short-circuits the regular flow ----------------
    if watch:
        if json_mode or sarif_mode:
            # Streaming structured output isn't a sensible mental model
            # for a continuous loop. Fail fast rather than emit
            # malformed concatenated payloads.
            raise click.UsageError("--watch is not compatible with --json or --sarif.")
        if check_external:
            # Polling external URLs every interval would hammer servers
            # and trip rate limits. The user almost certainly meant
            # "watch local refs, run --check-external manually before
            # commit". Refuse rather than silently mis-behave.
            raise click.UsageError(
                "--watch is not compatible with --check-external. Run external link checks as a separate one-shot scan."
            )
        if fix is not None:
            # Auto-rewriting source files behind the user's back inside
            # a poll loop is a foot-gun. Watch is informational; --fix
            # is a deliberate action.
            raise click.UsageError("--watch is not compatible with --fix.")

        # Load the baseline once at watch start (if any) so the loop
        # filters every rescan against the same reference set. Re-loading
        # on every cycle would be wasted work — the baseline file is
        # frozen.
        baseline_set: set[str] | None = None
        if baseline_from:
            loaded = _load_baseline(baseline_from)
            baseline_set = loaded if loaded else None

        scan_kwargs = dict(
            include_excluded=include_excluded,
            check_absolute_routes=check_absolute_routes,
            ignore_source=tuple(ignore_source),
            ignore_target=tuple(ignore_target),
            check_anchors=not no_anchors,
            scan_bare_backticks=scan_bare_backticks,
        )

        def _on_initial(stale_by_target_, files_scanned_, refs_seen_):
            click.echo(f"WATCH: monitoring {project_root} (interval {watch_interval}s) — Ctrl+C to exit\n")
            count = sum(len(v) for v in stale_by_target_.values())
            tgt_count = len(stale_by_target_)
            if count == 0:
                click.echo(f"  initial: clean ({refs_seen_} refs across {files_scanned_} files)\n")
            else:
                click.echo(f"  initial: {count} stale ref(s) across {tgt_count} target(s)\n")

        def _on_delta(added, resolved, current_findings):
            from datetime import datetime

            stamp = datetime.now().strftime("%H:%M:%S")
            if not added and not resolved:
                # Files changed but findings identical — silent.
                return
            click.echo(f"--- {stamp} (Δ +{len(added)} / −{len(resolved)} · total {len(current_findings)}) ---")
            for target, source_file, line, kind in sorted(added):
                click.echo(f"+ {_format_finding_line(target, source_file, line, kind)[2:]}")
            for target, source_file, line, kind in sorted(resolved):
                click.echo(f"- {_format_finding_line(target, source_file, line, kind)[2:]}")
            click.echo()

        _run_watch_loop(
            project_root,
            interval=watch_interval,
            scan_kwargs=scan_kwargs,
            on_initial=_on_initial,
            on_delta=_on_delta,
            baseline=baseline_set,
        )
        return

    t_start = time.perf_counter()
    stale_by_target, files_scanned, refs_seen, basename_idx, anchor_cache = _scan_project(
        project_root,
        include_excluded=include_excluded,
        check_absolute_routes=check_absolute_routes,
        ignore_source=tuple(ignore_source),
        ignore_target=tuple(ignore_target),
        check_anchors=not no_anchors,
        scan_bare_backticks=scan_bare_backticks,
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

    # ---- External link check ----------------------------------------
    external_meta: dict[str, int] = {}
    if check_external:
        # Parse the multi-value flags. ``--external-allow-status 401,403``
        # and ``--external-allow-status 401 --external-allow-status 403``
        # both produce the same set; auth headers are split on the
        # first colon so values can contain colons (URLs in Bearer
        # tokens, etc).
        allow_status_parsed: list[int] = []
        for raw in external_allow_status:
            for token in str(raw).split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    allow_status_parsed.append(int(token))
                except ValueError:
                    continue
        auth_headers_parsed: list[tuple[str, str]] = []
        for raw in external_auth_header:
            if ":" not in raw:
                continue
            name, _, value = raw.partition(":")
            name = name.strip()
            value = value.strip()
            if name and value:
                auth_headers_parsed.append((name, value))

        external_findings, cache_meta = _scan_external_urls(
            project_root,
            include_excluded=include_excluded,
            ignore_source=tuple(ignore_source),
            timeout=external_timeout,
            concurrency=external_concurrency,
            cache_ttl=external_cache_ttl,
            allow_status=tuple(allow_status_parsed),
            auth_headers=tuple(auth_headers_parsed),
            insecure=external_insecure,
        )
        external_meta["external_checked"] = sum(
            1
            for _ in external_findings  # only failures end up here
        )
        external_meta["external_cache_hits"] = cache_meta["hits"]
        external_meta["external_cache_misses"] = cache_meta["misses"]
        # Group external findings under their URL as the synthetic target
        # so existing rendering / sort / aggregation paths handle them
        # naturally. The "target" is the URL itself.
        for f in external_findings:
            entry: dict = {
                "file": f["file"],
                "line": f["line"],
                "kind": "external",
                "raw": f["raw"],
                "status": f["status"],
            }
            if "redirect_chain" in f:
                entry["redirect_chain"] = f["redirect_chain"]
            if _matches_any_glob(f["raw"], tuple(ignore_target)):
                continue
            stale_by_target.setdefault(f["raw"], []).append(entry)
        external_meta["external_findings"] = sum(
            len(v) for k, v in stale_by_target.items() if k.startswith(("http://", "https://"))
        )

    # ---- Baseline filter ----------------------------------------
    baseline_meta: dict[str, int] = {}
    if baseline_from:
        baseline = _load_baseline(baseline_from)
        if baseline:
            pre_count = sum(len(v) for v in stale_by_target.values())
            stale_by_target = _filter_against_baseline(stale_by_target, baseline)
            post_count = sum(len(v) for v in stale_by_target.values())
            baseline_meta = {
                "baseline_size": len(baseline),
                "filtered_out": pre_count - post_count,
            }

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
            hint = _hint_for_target(tgt, sources, hint_ctx, anchor_cache=anchor_cache)
            if hint is not None:
                item["hint"] = hint
                # Backwards-compat: keep the old flat ``rename_hint`` field.
                item["rename_hint"] = hint["target"]
        return item

    full_targets_with_hints = [_build_item(tgt, srcs, all_sources=True) for tgt, srcs in sorted_targets]
    displayed = [_build_item(tgt, srcs, all_sources=False) for tgt, srcs in sorted_targets[:limit]]

    # ---- Aggregations agents/dashboards want ─────────────────────────
    by_kind: dict[str, int] = defaultdict(int)
    by_confidence: dict[str, int] = defaultdict(int)
    fixable_count = 0
    for item in full_targets_with_hints:
        # Confidence band: HIGH / MEDIUM / LOW / NONE.
        hint = item.get("hint")
        confidence = hint["confidence"] if hint else "NONE"
        by_confidence[confidence] += 1
        if confidence == "HIGH" and item["sources"] and item["sources"][0]["kind"] != "anchor":
            # Anchor findings have no rename target — they aren't fixable.
            fixable_count += 1
        # Per-kind tallies are taken from each source so a single target
        # with mixed kinds (e.g. one md_inline + one html_attr) is
        # represented accurately.
        for s in item["sources"]:
            by_kind[s["kind"]] += 1

    if target_count == 0:
        verdict = f"all refs resolve · {refs_seen} checked · {files_scanned} files · {scan_seconds}s"
    else:
        anchor_note = f" · {anchor_findings} anchor" if anchor_findings else ""
        fix_note = f" · {fixable_count} auto-fixable" if fixable_count else ""
        diff_note = f" · diff base {diff_info['base_sha'][:7]}" if diff_info else ""
        verdict = (
            f"{total_refs} stale ref(s) · {target_count} missing target(s){anchor_note}{fix_note}{diff_note} · "
            f"{refs_seen} refs checked · {files_scanned} files · {scan_seconds}s"
        )

    # ---- Baseline write side --------------------------------------
    # Save BEFORE applying the baseline-from filter result to the output
    # paths so the saved file represents the unfiltered findings — that
    # way ``--baseline-save x.json`` followed by ``--baseline-from x.json``
    # gives a "no findings" verdict on a clean repo. We use the
    # post-kind-filter, post-diff-filter ``stale_by_target`` because
    # those filters reflect the user's intent for what counts as a
    # finding in their workflow.
    if baseline_save:
        _save_baseline(stale_by_target, baseline_save)

    # ---- Actionable next steps (consumed by JSON callers + text mode)
    next_steps = suggest_next_steps(
        "stale-refs",
        {
            "missing_targets": target_count,
            "fixable_count": fixable_count,
            "anchor_findings": anchor_findings,
            "by_confidence": dict(by_confidence),
        },
    )

    # ---- --fix mode short-circuits all other output paths
    if fix is not None:
        refused_log: list[str] = []
        edits_by_source = _build_fix_edits(
            full_targets_with_hints,
            project_root,
            include_medium=fix_medium,
            refused_log=refused_log,
        )
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
                                "refused_count": len(refused_log),
                            },
                            budget=token_budget,
                            diff=diff_text,
                            refused=refused_log,
                        )
                    )
                )
            else:
                click.echo(
                    f"VERDICT: --fix preview · {edits_planned} edit(s) across "
                    f"{files_touched} file(s) (HIGH-confidence hints only)\n"
                )
                if refused_log:
                    click.echo(
                        f"Refused {len(refused_log)} unsafe rewrite(s) — these "
                        "would have replaced a working link with a broken one "
                        "or produced a double-prefix path:"
                    )
                    for line in refused_log[:10]:
                        click.echo(f"  {line}")
                    if len(refused_log) > 10:
                        click.echo(f"  (+{len(refused_log) - 10} more)")
                    click.echo()
                if not edits_planned:
                    if target_count == 0:
                        click.echo("No findings to fix — all references resolve.")
                    else:
                        click.echo(
                            f"0 fixable / {target_count} total finding(s). "
                            "No HIGH-confidence rename hints — review manually "
                            "or use `--ignore` / `--ignore-target` to suppress "
                            "intentional dangling references (CHANGELOG history, "
                            "user-creatable optional configs, etc.)."
                        )
                else:
                    click.echo(diff_text or "(empty diff)")
            return
        # fix == "apply"
        files_written, edits_applied, locked_files = _apply_fixes_in_place(project_root, edits_by_source)
        # Smarter-3: log accepted hints to .roam/hint-acceptances.jsonl
        # so future scans / future smarter ranker can learn which
        # provider+source combinations the user actually accepts. The
        # file is append-only JSONL — one line per acceptance, with
        # the source provider, target, and timestamp. Best-effort: any
        # IO failure is swallowed (this is observability, not gating).
        if files_written > 0:
            _log_hint_acceptances(project_root, full_targets_with_hints, edits_by_source)
        lock_note = ""
        if locked_files:
            lock_note = f" · {len(locked_files)} file(s) skipped due to file locks"
        refuse_note = f" · {len(refused_log)} unsafe rewrite(s) refused" if refused_log else ""
        msg = f"--fix apply · wrote {edits_applied} edit(s) to {files_written} file(s){lock_note}{refuse_note}"
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
                            "files_locked": len(locked_files),
                            "missing_targets": target_count,
                            "stale_refs": total_refs,
                            "scan_seconds": scan_seconds,
                            "refused_count": len(refused_log),
                        },
                        budget=token_budget,
                        locked_files=locked_files,
                        refused=refused_log,
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {msg}")
            if refused_log:
                click.echo(
                    f"Refused {len(refused_log)} unsafe rewrite(s) (would have "
                    "replaced a working link with a broken one or produced a "
                    "double-prefix path):"
                )
                for line in refused_log[:10]:
                    click.echo(f"  {line}")
                if len(refused_log) > 10:
                    click.echo(f"  (+{len(refused_log) - 10} more)")
            if locked_files:
                click.echo(
                    f"Skipped {len(locked_files)} file(s) — another process "
                    "held them locked (common on Windows during open editor "
                    "sessions). Close the file(s) and re-run, or use `git "
                    "status` to see which:"
                )
                for path in sorted(locked_files)[:10]:
                    click.echo(f"  {path}")
                if len(locked_files) > 10:
                    click.echo(f"  (+{len(locked_files) - 10} more)")
            if files_written:
                click.echo("Re-run `roam stale-refs` to confirm and address remaining findings.")
            else:
                if target_count == 0:
                    click.echo("No findings to fix — all references resolve.")
                else:
                    click.echo(
                        f"0 fixable / {target_count} total finding(s). Run `roam stale-refs --fix preview` to inspect."
                    )
        return

    # ---- --attest writes an in-toto v1 statement before any other output
    # mode. Must run BEFORE sarif/json/text branches so a CI run that
    # uses ``--sarif --attest`` together still produces both artifacts.
    #
    # W126: never crash the scan because the attest target is unwritable
    # (parent is a file, perms denied, etc.). The scan already completed;
    # we surface the failure as structured state and keep going.
    attest_error: str | None = None
    attest_written: bool = False
    if attest_path:
        attest_summary = {
            "verdict": verdict,
            "missing_targets": target_count,
            "stale_refs": total_refs,
            "files_scanned": files_scanned,
            "refs_checked": refs_seen,
            "scan_seconds": scan_seconds,
            "sort_by": sort_by,
            "anchor_findings": anchor_findings,
            "fixable_count": fixable_count,
            "by_kind": dict(by_kind),
            "by_confidence": dict(by_confidence),
        }
        statement = build_stale_refs_attestation(
            project_root=project_root,
            summary=attest_summary,
            targets=full_targets_with_hints,
            findings=[s for srcs in stale_by_target.values() for s in srcs],
        )
        # Canonical form so the digest is reproducible across runs.
        rendered = json.dumps(statement, sort_keys=True, separators=(",", ":"))
        if attest_path == "-":
            # In SARIF/JSON modes, the stdout channel is reserved for
            # the structured envelope — emitting the attestation there
            # would corrupt it. Fall back to stderr so the artefact
            # still lands somewhere observable.
            if sarif_mode or json_mode:
                click.echo(rendered, err=True)
            else:
                click.echo(rendered)
            attest_written = True
        else:
            atomic_path = Path(attest_path)
            try:
                atomic_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(atomic_path, rendered + "\n")
                attest_written = True
            except OSError as exc:
                # FileExistsError / NotADirectoryError / PermissionError /
                # IsADirectoryError all subclass OSError. The scan itself
                # is complete and useful; treat attestation as a non-fatal
                # side-channel failure.
                attest_error = f"could not create attestation path {attest_path}: {exc}"
                # Surface to stderr so text-mode and JSON-mode consumers
                # both see the warning regardless of which output path is
                # taken below.
                click.echo(f"Warning: {attest_error}", err=True)

    # ---- --github-summary writes a markdown table for GitHub Actions ---
    # Runs alongside SARIF / JSON / text just like ``--attest``.
    if github_summary_path:
        try:
            md = _render_github_step_summary(
                verdict=verdict,
                targets=full_targets_with_hints,
                files_scanned=files_scanned,
                refs_seen=refs_seen,
            )
            sp = Path(github_summary_path)
            sp.parent.mkdir(parents=True, exist_ok=True)
            # GITHUB_STEP_SUMMARY semantics: append, not overwrite — a
            # workflow with multiple steps each writing a section ends
            # up with a stacked summary on the run page.
            with open(sp, "a", encoding="utf-8") as fh:
                fh.write(md)
        except OSError:
            # Best-effort: a write failure here shouldn't fail CI.
            pass

    # W126: helper for the post-output exit policy. Gate-failure (5) wins
    # if there are findings; otherwise an attestation failure under --gate
    # promotes to EXIT_PARTIAL (6) so CI can distinguish a clean-but-warned
    # run from a healthy run. Without --gate, attestation errors are
    # informational only (exit 0) — they're already in the envelope.
    def _exit_with_policy() -> None:
        if gate and target_count > 0:
            ctx.exit(5)
        if gate and attest_error:
            from roam.exit_codes import EXIT_PARTIAL

            ctx.exit(EXIT_PARTIAL)

    if sarif_mode:
        from roam.output.sarif import stale_refs_to_sarif, write_sarif

        click.echo(write_sarif(stale_refs_to_sarif(full_targets_with_hints)))
        _exit_with_policy()
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
            "fixable_count": fixable_count,
            "by_kind": dict(by_kind),
            "by_confidence": dict(by_confidence),
            "next_steps": next_steps,
        }
        if attest_path:
            summary["attestation_path"] = "stdout" if attest_path == "-" else str(Path(attest_path))
            # W126: surface attestation success/failure as structured state
            # so an agent doesn't have to parse stderr or guess.
            summary["attest_status"] = "ok" if attest_written else "failed"
        if attest_error:
            summary["attest_error"] = attest_error
        if diff_info:
            summary["diff_base"] = diff_info["base_sha"]
            summary["diff_base_ref"] = diff_info["base_ref"]
            summary["diff_changed_files"] = int(diff_info["changed_files"])
            summary["diff_deleted_files"] = int(diff_info["deleted_files"])
        if diff_warning:
            summary["diff_warning"] = diff_warning
        if baseline_meta:
            summary["baseline_size"] = baseline_meta["baseline_size"]
            summary["baseline_filtered_out"] = baseline_meta["filtered_out"]
        if baseline_save:
            summary["baseline_saved_to"] = baseline_save
        if external_meta:
            summary["external_findings"] = external_meta.get("external_findings", 0)
        if with_candidates:
            # Sample repo paths for the LLM enricher: prefer prose-shaped
            # files (md/rst/txt/html) since those are by far the most
            # common rename targets in doc references. Cap at 500 to stay
            # well under any reasonable sampling token budget.
            prose_paths = [p for p in basename_idx_paths(basename_idx) if _is_prose_path(p)]
            other_paths = [p for p in basename_idx_paths(basename_idx) if not _is_prose_path(p)]
            sample = (prose_paths[:300] + other_paths[:200])[:500]
            summary["repo_paths_sample"] = sorted(sample)
        click.echo(
            to_json(
                json_envelope(
                    "stale-refs",
                    summary=summary,
                    budget=token_budget,
                    targets=displayed,
                )
            )
        )
        _exit_with_policy()
        return

    # --- Text output ---
    if diff_warning:
        click.echo(diff_warning, err=True)
    click.echo(f"VERDICT: {verdict}\n")
    if target_count == 0:
        click.echo(f"Scanned {files_scanned} files, checked {refs_seen} references — all targets exist.")
        if next_steps:
            click.echo()
            click.echo(format_next_steps_text(next_steps))
        _exit_with_policy()
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
        if next_steps:
            click.echo()
            click.echo(format_next_steps_text(next_steps))
        _exit_with_policy()
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

    if next_steps:
        click.echo()
        click.echo(format_next_steps_text(next_steps))

    _exit_with_policy()
