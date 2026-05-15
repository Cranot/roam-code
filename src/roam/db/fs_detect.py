"""Cloud-synced filesystem detection (W127).

Path-substring heuristics that name *which* cloud-sync provider is in play
when a project (or its ``.roam/`` directory) lives under a synced root.

This complements two existing pieces of the substrate:

* ``connection._is_cloud_synced`` — boolean used at ``get_connection`` time
  to switch the SQLite journal from WAL to DELETE + EXCLUSIVE locking so
  the sync agent can't grab the WAL/SHM files mid-write.
* ``cmd_doctor._check_cloud_sync`` — advisory doctor check that reports
  the project root sits on a synced folder.

This module adds the *user-facing init warning* surface: ``roam init`` now
calls :func:`detect_cloud_sync` on the freshly-created ``.roam/`` directory
and emits a one-line advisory naming the provider plus the remediation
(set ``ROAM_DB_DIR`` to a local path).

The heuristic is path-substring only. It catches ~95% of real cases on
Windows / macOS / Linux without reading NTFS file attributes or polling
the OneDrive process. A ``None`` return is NOT a guarantee that no sync
is in play — custom Dropbox mount points or relocated OneDrive roots are
not detectable from the path alone.

The markers below match the same set the doctor advisory and the
connection-time WAL fallback use, in a single place. Three callers
share one source of truth so a new provider only has to be added here.
"""

from __future__ import annotations

from pathlib import Path

# Provider-named markers. Order is significant: ``OneDrive`` must precede
# ``Google Drive`` only because the substrings are anchored by the slash-
# prefixed path form below, but listing them in alphabetical-by-provider
# order keeps the diff readable.
#
# Each entry is ``(provider_name, [markers...])``. Markers are matched
# case-insensitively against the resolved, forward-slash-normalised path.
# Variants live next to their canonical provider so a corporate OneDrive
# install (``OneDrive - Acme``) reports as ``OneDrive``, not its own brand.
_PROVIDERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Windows OneDrive — personal (``OneDrive``) and corporate
    # (``OneDrive - Acme``). The corporate variant has a separator
    # after the brand so we match ``onedrive`` as a substring and
    # rely on the user's path not coincidentally containing that
    # word for unrelated reasons.
    ("OneDrive", ("/onedrive/", "/onedrive - ")),
    # Dropbox — same on every platform.
    ("Dropbox", ("/dropbox/",)),
    # Google Drive for Desktop. ``my drive`` is the per-account root
    # under ``Google Drive`` on macOS and Windows; we match both so
    # paths like ``G:/My Drive/repo`` are caught even when the parent
    # ``Google Drive`` folder isn't in the resolved path.
    ("Google Drive", ("/google drive/", "/my drive/", "/googledrive/")),
    # iCloud Drive. Three forms in the wild:
    #   * macOS: ~/Library/Mobile Documents/com~apple~CloudDocs/...
    #   * macOS Finder display name: iCloud Drive (rare in resolved path)
    #   * Windows iCloud client: %USERPROFILE%/iCloudDrive/...
    (
        "iCloud Drive",
        (
            "/icloud drive/",
            "/icloud~drive/",
            "/icloud/",
            "/icloudrive/",  # legacy spelling
            "/icloud drive ",
            "/library/mobile documents/",
            "/com~apple~clouddocs/",
        ),
    ),
    # Box Sync / Box Drive (corporate).
    ("Box", ("/box sync/", "/box/")),
    # pCloud Drive.
    ("pCloud", ("/pcloud/", "/pcloud drive/")),
    # Insync — third-party Google Drive client common on Linux.
    ("Insync", ("/insync/",)),
)


def _normalise(path: Path) -> str:
    """Return a lower-case, forward-slash-normalised, slash-bracketed view of *path*.

    Bracketing with leading + trailing ``/`` means a marker like
    ``/onedrive/`` matches when ``OneDrive`` is the *final* path component
    too — without the trailing slash, ``Path("C:/OneDrive").resolve()``
    would not match because there's no separator after the marker.
    """
    try:
        resolved = path.resolve()
    except OSError:
        # ``resolve`` can raise on broken symlinks / missing intermediate
        # dirs on some platforms. Fall back to the unresolved string so a
        # missing parent doesn't silently disable detection.
        resolved = path
    s = str(resolved).replace("\\", "/").lower()
    # Surround with slashes so terminal markers (``.../OneDrive``) match
    # the same substring patterns as mid-path markers (``.../OneDrive/foo``).
    return f"/{s.strip('/')}/"


def detect_cloud_sync(path: Path) -> str | None:
    """Return the cloud-sync provider name if *path* sits under a synced root.

    Returns the provider's canonical display name (``"OneDrive"``,
    ``"Dropbox"``, ``"Google Drive"``, ``"iCloud Drive"``, ``"Box"``,
    ``"pCloud"``, ``"Insync"``) or ``None`` when no marker matches.

    The check is path-substring only — fast, no I/O, no NTFS attribute
    reads. A ``None`` return is not a *guarantee* that no sync is in
    play (a custom mount point would be missed). Conversely, a non-None
    return does not mean writes will fail — it means the SQLite WAL
    journal would race with the sync agent if WAL mode were used.
    ``roam`` already mitigates this at ``get_connection`` time by
    switching to DELETE + EXCLUSIVE journal mode on cloud-synced paths
    (see ``connection.get_connection``); this function exists so callers
    (``init``, ``doctor``) can surface the situation to the user.
    """
    haystack = _normalise(path)
    for provider, markers in _PROVIDERS:
        for marker in markers:
            if marker in haystack:
                return provider
    return None


def cloud_sync_warning(provider: str, roam_dir: Path) -> str:
    """Format the one-line user-facing warning text for a cloud-sync hit.

    Shared by ``cmd_init`` (warns at init time) so the message stays
    consistent across surfaces. ``ROAM_DB_DIR`` is the real env-var
    knob — verified in ``src/roam/db/connection.py:get_db_path`` —
    so the remediation actually works when followed.
    """
    return (
        f"WARNING: .roam/ is on a {provider}-synced path ({roam_dir}). "
        f"SQLite WAL mode races with cloud-sync agents on writes; roam "
        f"falls back to DELETE journal + EXCLUSIVE locking automatically, "
        f"but indexing is slower and large repos can still hit transient "
        f"'database is locked' errors. To bypass, run "
        f"`roam config --use-local-cache` (persists a per-project local "
        f"DB dir), or set ROAM_DB_DIR to a local directory (e.g., "
        f"%LOCALAPPDATA%\\roam on Windows, ~/.cache/roam on Linux/macOS) "
        f"for one-shot use."
    )
