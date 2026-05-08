"""Minimal Language Server Protocol implementation surfacing ``stale-refs`` findings.

Spawned via ``roam lsp``. Speaks JSON-RPC 2.0 over stdio per the LSP
specification — no extra dependency. Editors that support LSP (VS Code,
Neovim, Sublime, JetBrains, Helix, …) can wire it in as a custom server
to get squiggly underlines on dangling markdown links / HTML hrefs /
backtick paths and missing anchors as you type.

Capabilities surfaced
---------------------

* ``textDocument/publishDiagnostics`` — per-document findings as
  Diagnostic objects with ``severity``, ``range``, and ``source``.
* (Future) ``textDocument/codeAction`` for HIGH-confidence rename
  rewrites — kept out of MVP to keep this server file under 350 lines.

Architecture
------------

* The server walks the project root once at startup to build a
  ``basename_idx`` and ``anchor_cache``. Subsequent ``didOpen`` /
  ``didChange`` / ``didSave`` events scan ONLY the changed file's
  in-memory buffer using those caches — fast enough to publish
  diagnostics on every keystroke.
* On ``didSave``, we additionally refresh the basename index in case
  the saved file added/removed a path the rest of the workspace
  references.

Limitations
-----------

The MVP intentionally skips:

* Workspace-wide rescans on file rename (use ``didChangeWatchedFiles``
  with the editor's file-rename event for that).
* Cross-file rename code actions (out of MVP scope).
* The ``--diff`` / ``--check-external`` flags (those are CI-side
  concerns, not editor-time).
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

import click

from roam.commands.cmd_stale_refs import (
    _BACKTICK_PATH_RE,
    _HTML_ATTR_RE,
    _MD_INLINE_RE,
    _MD_REFERENCE_RE,
    _PROSE_EXTS,
    _SCANNABLE_EXTS,
    _extract_fragment,
    _hint_for_target,
    _is_runtime_path,
    _resolve_backtick_target,
    _resolve_target,
)
from roam.commands.stale_refs_anchors import AnchorCache
from roam.commands.stale_refs_hints import HintContext
from roam.db.connection import find_project_root
from roam.index.discovery import discover_files

# ---------------------------------------------------------------------------
# JSON-RPC framing (LSP spec)
# ---------------------------------------------------------------------------


def _read_message(reader) -> dict | None:
    """Read one LSP-framed JSON-RPC message from *reader*; return None on EOF."""
    headers: dict[str, str] = {}
    while True:
        line = reader.readline()
        if not line:
            return None
        line = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            break
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    length_str = headers.get("content-length")
    if not length_str:
        return None
    try:
        length = int(length_str)
    except ValueError:
        return None
    body = reader.read(length)
    if len(body) < length:
        return None
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return None


def _write_message(writer, payload: dict) -> None:
    """Frame and write *payload* per LSP spec. Flush so the editor sees it."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    writer.write(header + body)
    writer.flush()


# ---------------------------------------------------------------------------
# URI ↔ path conversion
# ---------------------------------------------------------------------------


def _uri_to_path(uri: str, project_root: Path) -> str | None:
    """Convert a ``file://`` URI to a project-relative POSIX path."""
    import urllib.parse

    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file":
        return None
    abs_path = urllib.parse.unquote(parsed.path)
    if os.name == "nt" and abs_path.startswith("/") and len(abs_path) >= 3 and abs_path[2] == ":":
        # ``file:///C:/foo`` → ``C:/foo`` on Windows.
        abs_path = abs_path[1:]
    p = Path(abs_path)
    try:
        rel = p.resolve().relative_to(project_root.resolve())
    except (ValueError, OSError):
        return None
    return rel.as_posix()


def _line_col_from_offset(content: str, offset: int) -> tuple[int, int]:
    """Convert a 0-indexed character offset into LSP ``(line, character)``."""
    if offset <= 0:
        return 0, 0
    upto = content[: min(offset, len(content))]
    line = upto.count("\n")
    col = len(upto) - (upto.rfind("\n") + 1) if "\n" in upto else len(upto)
    return line, col


# ---------------------------------------------------------------------------
# Per-buffer scan (single-file scope)
# ---------------------------------------------------------------------------


def _scan_buffer_for_diagnostics(
    rel_path: str,
    content: str,
    project_root: Path,
    *,
    tracked_set: set[str],
    dir_set: set[str],
    basename_idx: dict[str, list[str]],
    anchor_cache: AnchorCache,
    hint_ctx: HintContext,
) -> list[dict]:
    """Scan ONE file's in-memory buffer; return LSP Diagnostic objects.

    The signature mirrors :func:`roam.commands.cmd_stale_refs._scan_project`
    but operates on a single buffer with caches passed in, so per-keystroke
    scans cost only the regex pass on the buffer's content.
    """
    ext = os.path.splitext(rel_path)[1].lower()
    if ext not in _SCANNABLE_EXTS:
        return []
    prose_mode = ext in _PROSE_EXTS

    diagnostics: list[dict] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        zero_based_line = lineno - 1
        # Markdown / HTML / backtick — same regex set as the scanner.
        for kind, regex in (
            ("md_inline", _MD_INLINE_RE),
            ("md_reference", _MD_REFERENCE_RE),
            ("html_attr", _HTML_ATTR_RE),
            ("backtick", _BACKTICK_PATH_RE),
        ):
            if kind in {"md_inline", "md_reference", "html_attr"} and not prose_mode:
                continue
            for m in regex.finditer(line):
                if kind == "html_attr":
                    raw_url = m.group("v1") or m.group("v2") or ""
                elif kind == "backtick":
                    raw_url = m.group("path")
                else:
                    raw_url = m.group("url")
                if not raw_url:
                    continue
                fragment = _extract_fragment(raw_url) if kind != "backtick" else ""

                # In-page anchor case (URL is just ``#fragment``).
                from roam.commands.cmd_stale_refs import _strip_url_decorations

                if kind != "backtick" and fragment and not _strip_url_decorations(raw_url).split("#", 1)[0]:
                    if AnchorCache.is_anchor_validatable(rel_path):
                        anchors = anchor_cache.anchors_for(rel_path)
                        if anchors is not None and fragment.lower() not in anchors:
                            diagnostics.append(
                                _make_diagnostic(
                                    line,
                                    zero_based_line,
                                    m,
                                    raw_url,
                                    f"Anchor '#{fragment}' not found in this file",
                                    severity=3,  # information
                                )
                            )
                    continue

                if kind == "backtick":
                    target = _resolve_backtick_target(
                        raw_url,
                        rel_path,
                        project_root,
                        basename_idx=basename_idx,
                        prose_mode=prose_mode,
                    )
                else:
                    target = _resolve_target(raw_url, rel_path, project_root)
                if target is None:
                    continue
                try:
                    rel_target = target.relative_to(project_root).as_posix()
                except ValueError:
                    continue
                if _is_runtime_path(rel_target):
                    continue

                target_exists = rel_target in tracked_set or rel_target in dir_set or target.exists()

                if target_exists:
                    if fragment and AnchorCache.is_anchor_validatable(rel_target):
                        anchors = anchor_cache.anchors_for(rel_target)
                        if anchors is not None and fragment.lower() not in anchors:
                            diagnostics.append(
                                _make_diagnostic(
                                    line,
                                    zero_based_line,
                                    m,
                                    raw_url,
                                    f"Anchor '#{fragment}' not found in '{rel_target}'",
                                    severity=3,
                                )
                            )
                    continue

                # Path finding: missing target.
                hint_text = ""
                hint = _hint_for_target(
                    rel_target,
                    [{"file": rel_path, "line": lineno, "kind": kind, "raw": raw_url}],
                    hint_ctx,
                    anchor_cache=anchor_cache,
                )
                rewrite_to = None
                if hint:
                    hint_text = f". Did you mean '{hint['target']}'? [{hint['confidence']} · {hint['source']}]"
                    # Only HIGH-confidence hints earn an editable Quick
                    # Fix. MEDIUM/LOW hints surface in the message text
                    # but require a human decision — keeps the same
                    # safety bar as ``--fix apply`` on the CLI.
                    if hint["confidence"] == "HIGH":
                        rewrite_to = _rewrite_for_lsp(raw_url, hint["target"])
                diagnostics.append(
                    _make_diagnostic(
                        line,
                        zero_based_line,
                        m,
                        raw_url,
                        f"Reference target missing: '{rel_target}'{hint_text}",
                        severity=2,  # warning
                        rewrite_to=rewrite_to,
                    )
                )
    return diagnostics


def _rewrite_for_lsp(raw_url: str, hint_target: str) -> str:
    """Compute the literal rewrite text for a Quick Fix code action.

    Same logic shape as ``cmd_stale_refs._build_fix_edits`` — preserve
    fragments on path findings, substitute fragments only on anchor
    findings — but adapted to "what newText replaces the diagnostic
    range?" rather than "what str.replace pair?".
    """
    # Path finding with fragment in raw URL: append the fragment to
    # the new path so we don't drop in-target navigation.
    if "#" in raw_url and "#" not in hint_target:
        fragment = raw_url.split("#", 1)[1]
        return f"{hint_target}#{fragment}"
    return hint_target


def _make_diagnostic(
    line_text: str,
    zero_based_line: int,
    match: re.Match,
    raw_url: str,
    message: str,
    *,
    severity: int,
    rewrite_to: str | None = None,
) -> dict:
    """Build a single LSP Diagnostic object pointing at *match*'s span.

    When *rewrite_to* is given (only HIGH-confidence hints qualify),
    the resulting Diagnostic includes a ``data`` field carrying the
    proposed rewrite. The codeAction handler reads that data field to
    build a "Quick Fix" workspace edit — replace the diagnostic's
    range with ``rewrite_to``.
    """
    # Find the URL inside the match's overall span. Most regex groups
    # we care about (``url``, ``path``, ``v1``, ``v2``) carry the URL
    # itself; falling back to the full match keeps us robust.
    start = match.start()
    if raw_url and raw_url in line_text:
        start = line_text.find(raw_url)
    end = start + len(raw_url) if raw_url else match.end()
    diag: dict = {
        "range": {
            "start": {"line": zero_based_line, "character": start},
            "end": {"line": zero_based_line, "character": end},
        },
        "severity": severity,
        "source": "roam-stale-refs",
        "message": message,
    }
    if rewrite_to:
        diag["data"] = {"rewrite_to": rewrite_to, "raw": raw_url}
    return diag


# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------


class _ServerState:
    """Mutable per-session caches shared by every request handler."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.tracked_set: set[str] = set()
        self.dir_set: set[str] = set()
        self.basename_idx: dict[str, list[str]] = {}
        self.anchor_cache = AnchorCache(project_root)
        self.hint_ctx = HintContext(project_root=project_root, basename_idx={})
        self.lock = threading.Lock()
        # URIs the client has opened — used to re-publish diagnostics
        # after a workspace-wide change (file rename, delete, etc).
        self.open_buffers: dict[str, str] = {}
        # Set during ``initialize`` from client capabilities — gates the
        # ``client/registerCapability`` call below.
        self.client_supports_file_watcher: bool = False
        # Monotonically increasing ID for outbound requests we send to
        # the client (registration, applyEdit, etc).
        self._outbound_id_counter = 1000
        self.refresh_index()

    def next_outbound_id(self) -> int:
        self._outbound_id_counter += 1
        return self._outbound_id_counter

    def refresh_index(self) -> None:
        """Re-derive tracked_set / dir_set / basename_idx from the workspace."""
        from collections import defaultdict

        with self.lock:
            try:
                files = discover_files(self.project_root)
            except Exception:
                files = []
            self.tracked_set = set(files)
            self.dir_set = set()
            for p in files:
                parent = os.path.dirname(p)
                while parent:
                    self.dir_set.add(parent)
                    parent = os.path.dirname(parent)
            basename_idx: dict[str, list[str]] = defaultdict(list)
            for p in files:
                basename_idx[os.path.basename(p)].append(p)
            self.basename_idx = dict(basename_idx)
            self.hint_ctx.basename_idx = self.basename_idx
            # Reset anchor cache so renamed/edited targets re-parse.
            self.anchor_cache = AnchorCache(self.project_root)


# ---------------------------------------------------------------------------
# Request dispatch
# ---------------------------------------------------------------------------


def _server_version() -> str:
    """Read the package version dynamically so the LSP serverInfo
    doesn't drift from the installed roam-code release."""
    try:
        from roam import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def _handle_initialize(state: _ServerState, msg: dict, writer) -> None:
    # Record whether the client supports dynamic file-watcher
    # registration. Most editors do (VS Code, Neovim, Helix) but a few
    # don't, and registering against a non-supporting client leaks an
    # error response. Default to ``False`` so we err on the side of
    # quiet.
    params = msg.get("params") or {}
    cap = (params.get("capabilities") or {}).get("workspace") or {}
    dyn_reg = (cap.get("didChangeWatchedFiles") or {}).get("dynamicRegistration")
    state.client_supports_file_watcher = bool(dyn_reg)

    capabilities = {
        "textDocumentSync": {"openClose": True, "change": 1, "save": True},
        # Diagnostic options. We push diagnostics on every change; clients
        # don't need to pull.
        "diagnosticProvider": {
            "interFileDependencies": False,
            "workspaceDiagnostics": False,
        },
        # Code actions: HIGH-confidence findings carry a rewrite suggestion
        # in their ``data`` field; the editor's Quick Fix menu surfaces
        # "Replace with <hint.target>" so users can apply rename hints
        # with one keystroke.
        "codeActionProvider": {
            "codeActionKinds": ["quickfix"],
        },
        # Workspace capabilities: file watcher registration is dynamic
        # (issued post-``initialized`` via ``client/registerCapability``)
        # and ``willRenameFiles`` is static. The latter lets editors
        # ask us "the user is about to rename A → B; do you want to
        # contribute any workspace edits?" — we scan the workspace for
        # references to A and propose updating them all to B.
        "workspace": {
            "workspaceFolders": {"supported": False},
            "fileOperations": {
                "willRename": {
                    "filters": [
                        {"scheme": "file", "pattern": {"glob": "**/*"}},
                    ]
                },
            },
        },
    }
    _write_message(
        writer,
        {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {
                "capabilities": capabilities,
                "serverInfo": {
                    "name": "roam-stale-refs-lsp",
                    "version": _server_version(),
                },
            },
        },
    )


def _handle_initialized(state: _ServerState, writer) -> None:
    """Register a workspace file-watcher so the client notifies us on disk changes.

    Called once per session, immediately after the client sends
    ``initialized`` (the post-initialize handshake). The client doesn't
    watch files for us by default — we have to register a glob and
    receive ``workspace/didChangeWatchedFiles`` events as they happen.

    We only register if the client advertised
    ``workspace.didChangeWatchedFiles.dynamicRegistration: true`` in
    the initialize handshake — sending the request to a non-supporting
    client just generates an error. Static-watcher clients fall back
    to relying on ``didSave`` which works fine for in-editor changes.

    The glob ``**/*`` is intentionally broad: a markdown link could
    point at literally any file in the repo, so any disk change might
    invalidate a diagnostic. The handler's only job is to call
    ``state.refresh_index()`` and re-scan open buffers, both cheap.
    """
    if not state.client_supports_file_watcher:
        return
    request_id = state.next_outbound_id()
    _write_message(
        writer,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "client/registerCapability",
            "params": {
                "registrations": [
                    {
                        "id": "roam-stale-refs-watcher",
                        "method": "workspace/didChangeWatchedFiles",
                        "registerOptions": {
                            "watchers": [
                                {"globPattern": "**/*", "kind": 7}  # 7 = create|change|delete
                            ]
                        },
                    }
                ]
            },
        },
    )


def _publish_diagnostics(writer, uri: str, diagnostics: list[dict]) -> None:
    """Send ``textDocument/publishDiagnostics`` for *uri* with *diagnostics*.

    Sending an empty list clears any previously-published diagnostics
    for that URI on the client side — the LSP spec contract.
    """
    _write_message(
        writer,
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "diagnostics": diagnostics},
        },
    )


def _handle_did_open_or_change(state: _ServerState, msg: dict, writer) -> None:
    params = msg.get("params") or {}
    doc = params.get("textDocument") or {}
    uri = doc.get("uri", "")
    rel_path = _uri_to_path(uri, state.project_root)
    if rel_path is None:
        # File is outside the project root — publish empty diagnostics
        # so any prior state on the client side clears, then bail. This
        # prevents stale red squiggles when the user switches between
        # projects in a multi-root workspace.
        if uri:
            _publish_diagnostics(writer, uri, [])
        return
    # Pull the buffer content. didOpen has it in textDocument.text;
    # didChange has it in contentChanges[0].text (full sync mode).
    content = doc.get("text")
    if content is None:
        changes = params.get("contentChanges") or []
        if changes:
            content = changes[-1].get("text", "")
    if content is None:
        return

    # Track the buffer so workspace-level file changes can re-scan it.
    state.open_buffers[uri] = content

    diagnostics = _scan_buffer_for_diagnostics(
        rel_path,
        content,
        state.project_root,
        tracked_set=state.tracked_set,
        dir_set=state.dir_set,
        basename_idx=state.basename_idx,
        anchor_cache=state.anchor_cache,
        hint_ctx=state.hint_ctx,
    )
    _publish_diagnostics(writer, uri, diagnostics)


def _handle_did_close(state: _ServerState, msg: dict, writer) -> None:
    """Drop the buffer from ``open_buffers`` and clear its diagnostics."""
    params = msg.get("params") or {}
    doc = params.get("textDocument") or {}
    uri = doc.get("uri", "")
    if uri and uri in state.open_buffers:
        del state.open_buffers[uri]
    if uri:
        _publish_diagnostics(writer, uri, [])


def _rescan_all_open_buffers(state: _ServerState, writer) -> None:
    """Re-scan every open buffer. Used after a workspace-level file change.

    The watched-files event from the client tells us a file appeared,
    moved, or vanished. References in OTHER open buffers may now resolve
    differently — a previously-broken link may be correct, or
    a previously-correct link may break. Re-publish diagnostics for
    every tracked buffer so the editor stays consistent.
    """
    for uri, content in list(state.open_buffers.items()):
        rel_path = _uri_to_path(uri, state.project_root)
        if rel_path is None:
            continue
        diagnostics = _scan_buffer_for_diagnostics(
            rel_path,
            content,
            state.project_root,
            tracked_set=state.tracked_set,
            dir_set=state.dir_set,
            basename_idx=state.basename_idx,
            anchor_cache=state.anchor_cache,
            hint_ctx=state.hint_ctx,
        )
        _publish_diagnostics(writer, uri, diagnostics)


def _handle_did_change_watched_files(state: _ServerState, msg: dict, writer) -> None:
    """Refresh the workspace index and re-publish diagnostics for open buffers.

    The LSP client sends ``workspace/didChangeWatchedFiles`` whenever a
    file matching one of our registered watchers is created, changed,
    or deleted on disk — even when the change happens outside the
    editor (e.g. ``git checkout``, file manager rename). Without this
    handler, a user running ``git pull`` would keep seeing stale
    diagnostics until they reopened the file.

    LSP file event types: 1=created, 2=changed, 3=deleted. We treat all
    three identically — every change invalidates the basename index
    and (potentially) per-target anchor cache, so we just refresh
    everything.
    """
    state.refresh_index()
    _rescan_all_open_buffers(state, writer)


def _handle_did_save(state: _ServerState, msg: dict, writer) -> None:
    # On save, the workspace might have grown (new file) or shrunk (deletion
    # via the editor's UI). Refresh the index so subsequent scans see it.
    state.refresh_index()
    # Re-scan the saved document so the diagnostics reflect the post-save
    # workspace state (relevant when other files now resolve targets that
    # didn't before, etc.).
    _handle_did_open_or_change(state, msg, writer)
    # And re-scan every OTHER open buffer too — saving a renamed file
    # commonly fixes references to it from elsewhere in the workspace.
    saved_uri = (msg.get("params") or {}).get("textDocument", {}).get("uri")
    for uri, content in list(state.open_buffers.items()):
        if uri == saved_uri:
            continue
        rel_path = _uri_to_path(uri, state.project_root)
        if rel_path is None:
            continue
        diagnostics = _scan_buffer_for_diagnostics(
            rel_path,
            content,
            state.project_root,
            tracked_set=state.tracked_set,
            dir_set=state.dir_set,
            basename_idx=state.basename_idx,
            anchor_cache=state.anchor_cache,
            hint_ctx=state.hint_ctx,
        )
        _publish_diagnostics(writer, uri, diagnostics)


def _handle_will_rename_files(state: _ServerState, msg: dict, writer) -> None:
    """Compute a WorkspaceEdit that updates every reference to a renamed file.

    The client invokes ``workspace/willRenameFiles`` BEFORE doing the
    actual rename. Each rename is ``{oldUri, newUri}``; we walk every
    text file in the workspace, find references that resolve to the
    old path, and emit a TextEdit replacing the raw URL with the new
    relative path (computed from each source file's perspective so
    relative links remain relative).

    Returns the empty WorkspaceEdit when the rename has zero impact —
    the editor still proceeds with the rename, but no extra edits land.
    The reply must be a ``WorkspaceEdit`` (or null), per LSP §3.18.
    """
    params = msg.get("params") or {}
    files = params.get("files") or []

    edits_by_uri: dict[str, list[dict]] = {}

    for entry in files:
        if not isinstance(entry, dict):
            continue
        old_uri = entry.get("oldUri", "")
        new_uri = entry.get("newUri", "")
        old_rel = _uri_to_path(old_uri, state.project_root)
        new_rel = _uri_to_path(new_uri, state.project_root)
        if not old_rel or not new_rel or old_rel == new_rel:
            continue

        for src_rel in state.tracked_set:
            ext = os.path.splitext(src_rel)[1].lower()
            if ext not in _SCANNABLE_EXTS:
                continue
            src_abs = state.project_root / src_rel
            try:
                content = src_abs.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_edits = _collect_rename_edits(
                src_rel,
                content,
                state.project_root,
                old_rel=old_rel,
                new_rel=new_rel,
                basename_idx=state.basename_idx,
            )
            if not file_edits:
                continue
            src_uri = (state.project_root / src_rel).resolve().as_uri()
            edits_by_uri.setdefault(src_uri, []).extend(file_edits)

    workspace_edit: dict = {}
    if edits_by_uri:
        workspace_edit = {"changes": edits_by_uri}

    _write_message(
        writer,
        {"jsonrpc": "2.0", "id": msg.get("id"), "result": workspace_edit},
    )


def _collect_rename_edits(
    src_rel: str,
    content: str,
    project_root: Path,
    *,
    old_rel: str,
    new_rel: str,
    basename_idx: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Return TextEdit[] that update references to *old_rel* → *new_rel*.

    Walks every detected reference in *content*, asks the same resolver
    used for diagnostics whether it lands on *old_rel*, and if so emits
    a TextEdit replacing the raw URL span with the resolved-relative
    path of *new_rel* (preserving fragment when present).
    """
    ext = os.path.splitext(src_rel)[1].lower()
    if ext not in _SCANNABLE_EXTS:
        return []
    prose_mode = ext in _PROSE_EXTS
    edits: list[dict] = []
    old_rel_normalised = old_rel.replace("\\", "/")

    for lineno, line in enumerate(content.splitlines(), start=1):
        zero_based_line = lineno - 1
        for kind, regex in (
            ("md_inline", _MD_INLINE_RE),
            ("md_reference", _MD_REFERENCE_RE),
            ("html_attr", _HTML_ATTR_RE),
            ("backtick", _BACKTICK_PATH_RE),
        ):
            if kind in {"md_inline", "md_reference", "html_attr"} and not prose_mode:
                continue
            for m in regex.finditer(line):
                if kind == "html_attr":
                    raw_url = m.group("v1") or m.group("v2") or ""
                elif kind == "backtick":
                    raw_url = m.group("path")
                else:
                    raw_url = m.group("url")
                if not raw_url:
                    continue
                fragment = _extract_fragment(raw_url) if kind != "backtick" else ""
                if kind == "backtick":
                    target = _resolve_backtick_target(
                        raw_url,
                        src_rel,
                        project_root,
                        basename_idx=basename_idx or {},
                        prose_mode=prose_mode,
                    )
                else:
                    target = _resolve_target(raw_url, src_rel, project_root)
                if target is None:
                    continue
                try:
                    rel_target = target.relative_to(project_root).as_posix()
                except ValueError:
                    continue
                if rel_target != old_rel_normalised:
                    continue
                # The old reference lands on *old_rel*; replace with new_rel.
                # Preserve fragment + leading "./" style if present.
                new_url = new_rel
                if raw_url.startswith("./"):
                    new_url = "./" + new_url
                if fragment and "#" not in new_url:
                    new_url = f"{new_url}#{fragment}"
                start = m.start()
                if raw_url and raw_url in line:
                    start = line.find(raw_url)
                end = start + len(raw_url) if raw_url else m.end()
                edits.append(
                    {
                        "range": {
                            "start": {"line": zero_based_line, "character": start},
                            "end": {"line": zero_based_line, "character": end},
                        },
                        "newText": new_url,
                    }
                )
    return edits


def _handle_code_action(state: _ServerState, msg: dict, writer) -> None:
    """Return Quick Fix CodeAction[] for HIGH-confidence diagnostics.

    The editor calls ``textDocument/codeAction`` whenever the user
    triggers Quick Fix (Cmd+. on macOS, Ctrl+. elsewhere). We're given
    the URI + the diagnostics in range. For each diagnostic carrying a
    ``data.rewrite_to`` field (set by ``_make_diagnostic`` for HIGH-
    confidence findings), we synthesise a CodeAction with a
    ``WorkspaceEdit`` that replaces the diagnostic's range with the
    rewrite text.

    Editors then surface "Replace with <new>" in the Quick Fix menu.
    Selecting it triggers the editor's ``workspace/applyEdit`` flow —
    no further server round-trip needed.
    """
    params = msg.get("params") or {}
    text_doc = params.get("textDocument") or {}
    uri = text_doc.get("uri", "")
    context = params.get("context") or {}
    diagnostics = context.get("diagnostics") or []

    actions: list[dict] = []
    for diag in diagnostics:
        if not isinstance(diag, dict):
            continue
        data = diag.get("data") or {}
        rewrite = data.get("rewrite_to")
        if not rewrite or not isinstance(rewrite, str):
            continue
        actions.append(
            {
                "title": f"Replace with '{rewrite}'",
                "kind": "quickfix",
                "diagnostics": [diag],
                "edit": {
                    "changes": {
                        uri: [
                            {
                                "range": diag.get("range"),
                                "newText": rewrite,
                            }
                        ]
                    }
                },
            }
        )

    _write_message(
        writer,
        {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": actions,
        },
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@click.command("lsp")
@click.option(
    "--once",
    is_flag=True,
    default=False,
    hidden=True,
    help="(Test hook) handle one message then exit. Used by the test suite.",
)
def lsp(once: bool) -> None:
    """Run the roam-stale-refs language server on stdin/stdout (LSP).

    Wire it into your editor as a custom LSP server pointing at
    ``roam lsp``. Squiggly underlines on dangling markdown links and
    missing anchors will appear as you type.
    """
    project_root = find_project_root()
    state = _ServerState(project_root)
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer

    while True:
        msg = _read_message(reader)
        if msg is None:
            return
        method = msg.get("method") or ""
        try:
            if method == "initialize":
                _handle_initialize(state, msg, writer)
            elif method == "initialized":
                _handle_initialized(state, writer)
            elif method == "textDocument/didOpen":
                _handle_did_open_or_change(state, msg, writer)
            elif method == "textDocument/didChange":
                _handle_did_open_or_change(state, msg, writer)
            elif method == "textDocument/didClose":
                _handle_did_close(state, msg, writer)
            elif method == "textDocument/didSave":
                _handle_did_save(state, msg, writer)
            elif method == "workspace/didChangeWatchedFiles":
                _handle_did_change_watched_files(state, msg, writer)
            elif method == "workspace/willRenameFiles":
                _handle_will_rename_files(state, msg, writer)
            elif method == "textDocument/codeAction":
                _handle_code_action(state, msg, writer)
            elif method == "shutdown":
                _write_message(
                    writer,
                    {"jsonrpc": "2.0", "id": msg.get("id"), "result": None},
                )
            elif method == "exit":
                return
            else:
                # Unknown method. If it's a request (has ``id``), reply
                # with method-not-found; otherwise silently ignore.
                if "id" in msg:
                    _write_message(
                        writer,
                        {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "error": {"code": -32601, "message": f"Unknown method: {method}"},
                        },
                    )
        except Exception as exc:  # pragma: no cover - defensive
            if "id" in msg:
                _write_message(
                    writer,
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32603, "message": f"Internal error: {exc}"},
                    },
                )
        if once:
            return
