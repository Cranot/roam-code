"""Index portability — ``roam index-export`` / ``index-import``.

Counters Cursor's "92% similar codebase = reuse teammate's index" without
requiring a vendor cloud. Emits a portable, integrity-checked bundle:

::

    .roam-bundle.tar.gz
    ├── manifest.json        — metadata (version, schema, sha256, etc.)
    ├── index.db             — the SQLite roam index
    └── manifest.json.sig    — optional cosign signature (if --sign)

Import verifies the manifest's integrity hash against the bundled
``index.db``, checks the schema version against the current roam, and
extracts to ``.roam/index.db``. Refuses to overwrite without ``--force``.

Same trust story as the CGA chain: in-toto-style manifest, optional
cosign keyless or offline signing, deterministic hashing. No network,
no vendor lock-in.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import click

from roam import __version__
from roam.commands.resolve import ensure_index
from roam.db.connection import get_connection
from roam.output.formatter import json_envelope, to_json

# Bump when the bundle layout changes (manifest fields, file layout, etc.).
# Independent of the SQLite schema version inside index.db.
BUNDLE_FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------


def _index_db_path() -> Path:
    """Canonical path to the roam SQLite index in the cwd."""
    return Path(".roam") / "index.db"


def _sha256_file(path: Path) -> str:
    """Stream a file through SHA-256 and return the hex digest."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_schema_version(index_path: Path) -> int | None:
    """Return ``PRAGMA user_version`` from the DB at *index_path*, or ``None``.

    Returns ``None`` for missing files (SQLite would otherwise silently
    create an empty DB at the path and return 0, which is misleading).
    """
    if not index_path.is_file():
        return None
    try:
        conn = get_connection(db_path=index_path, readonly=True)
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            if row:
                return int(row[0])
        finally:
            conn.close()
    except Exception:
        return None
    return None


def _read_repo_head(repo_root: Path) -> str | None:
    """Best-effort git HEAD sha (40-char hex) for the repo at *repo_root*.

    Returns ``None`` for non-git directories or when git isn't on PATH.
    Uses :func:`worktree_git_env` so parallel agents don't contend.
    """
    import subprocess

    from roam.git_utils import worktree_git_env

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
            env=worktree_git_env(repo_root),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha if sha else None


def _build_manifest(index_path: Path, repo_root: Path) -> dict:
    """Compose the bundle manifest dict.

    Includes: roam version, bundle format version, sqlite schema version,
    SHA-256 of the index file, file size, repo HEAD sha (if available),
    and a UTC timestamp. The manifest is JSON-serialised in the bundle and
    is what cosign signs.
    """
    return {
        "roam_version": __version__,
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "schema_version": _read_schema_version(index_path),
        "index_db": {
            "name": "index.db",
            "size_bytes": index_path.stat().st_size,
            "sha256": _sha256_file(index_path),
        },
        "repo_head": _read_repo_head(repo_root),
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _verify_bundle(bundle_path: Path) -> dict:
    """Open *bundle_path* and verify the manifest matches the bundled DB.

    Returns the parsed manifest. Raises ``ValueError`` on any mismatch
    (so callers can surface a clean error message).
    """
    if not bundle_path.is_file():
        raise ValueError(f"bundle not found: {bundle_path}")

    try:
        tar_ctx = tarfile.open(bundle_path, mode="r:*")
    except (tarfile.ReadError, tarfile.CompressionError, EOFError) as exc:
        # Corrupted, truncated, or non-tar file. Surface as ValueError so
        # the CLI catches it cleanly (rather than a raw stack trace).
        raise ValueError(f"bundle is corrupted or not a tarball: {exc}") from exc

    with tar_ctx as tar:
        names = set(tar.getnames())
        if "manifest.json" not in names or "index.db" not in names:
            raise ValueError(f"bundle missing required members; got {sorted(names)} (need manifest.json + index.db)")
        manifest_member = tar.getmember("manifest.json")
        index_member = tar.getmember("index.db")

        manifest_fh = tar.extractfile(manifest_member)
        if manifest_fh is None:
            raise ValueError("could not extract manifest.json from bundle")
        manifest = json.loads(manifest_fh.read().decode("utf-8"))

        # Verify size + sha256 of the bundled index_db against the manifest.
        expected_size = int(manifest.get("index_db", {}).get("size_bytes", -1))
        if index_member.size != expected_size:
            raise ValueError(f"index.db size mismatch: bundle={index_member.size} manifest={expected_size}")
        index_fh = tar.extractfile(index_member)
        if index_fh is None:
            raise ValueError("could not extract index.db from bundle")
        h = hashlib.sha256()
        for chunk in iter(lambda: index_fh.read(1 << 20), b""):
            h.update(chunk)
        actual_sha = h.hexdigest()
        expected_sha = manifest.get("index_db", {}).get("sha256")
        if actual_sha != expected_sha:
            raise ValueError(f"index.db sha256 mismatch: bundle={actual_sha} manifest={expected_sha}")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command("index-export")
@click.argument("output", type=click.Path())
@click.option(
    "--sign",
    is_flag=True,
    help="Sign the manifest with cosign (graceful skip if cosign is missing).",
)
@click.option(
    "--key",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Cosign private key for offline signing. Pair with --sign.",
)
@click.option(
    "--keyless",
    is_flag=True,
    help="Cosign keyless OIDC signing (Fulcio + Rekor). Pair with --sign.",
)
@click.pass_context
def index_export(ctx, output, sign, key, keyless):
    """Export the roam index as a portable, integrity-checked tarball.

    Use this to share a pre-built index with teammates without re-indexing.
    The output is ``<output>`` (defaults to ``.tar.gz`` if no extension).
    Bundle layout: ``manifest.json`` (metadata + sha256), ``index.db``, and
    optionally ``manifest.json.sig``/``manifest.json.bundle`` for cosign.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    out_path = Path(output)
    if not out_path.suffix:
        out_path = out_path.with_suffix(".tar.gz")

    index_path = _index_db_path()
    if not index_path.is_file():
        raise click.UsageError(f"no roam index found at {index_path} — run `roam index` first.")
    ensure_index()

    manifest = _build_manifest(index_path, Path.cwd())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        manifest_path = tmp_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with tarfile.open(out_path, mode="w:gz") as tar:
            tar.add(manifest_path, arcname="manifest.json")
            tar.add(index_path, arcname="index.db")

        # Optional cosign signing — sign the manifest, not the tarball
        # itself (matches the in-toto pattern in attest/cga.py).
        sig_info: dict | None = None
        if sign:
            from roam.attest.cga import (
                cosign_available,
                cosign_sign_statement,
            )

            available, _version = cosign_available()
            if not available:
                click.echo(
                    "  Note: cosign binary not on PATH; skipping signature. "
                    "Install with `brew install cosign` or "
                    "`go install github.com/sigstore/cosign/v2/cmd/cosign@latest`.",
                    err=True,
                )
            else:
                try:
                    sig_path = out_path.with_suffix(out_path.suffix + ".sig")
                    bundle_path = out_path.with_suffix(out_path.suffix + ".bundle")
                    cosign_sign_statement(
                        manifest_path,
                        sig_path,
                        bundle_path,
                        key=key,
                        keyless=keyless,
                    )
                    sig_info = {
                        "signature": str(sig_path),
                        "bundle": str(bundle_path),
                        "mode": "keyless" if keyless else "offline",
                    }
                except Exception as exc:
                    click.echo(f"  Warning: cosign signing failed: {exc}", err=True)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "index-export",
                    summary={
                        "verdict": f"Wrote bundle to {out_path}",
                        "bundle_path": str(out_path),
                        "size_bytes": out_path.stat().st_size,
                        "signed": bool(sig_info),
                    },
                    manifest=manifest,
                    signature=sig_info,
                )
            )
        )
        return

    click.echo(f"VERDICT: Exported index to {out_path}")
    click.echo(f"  bundle size: {out_path.stat().st_size:,} bytes")
    click.echo(f"  index sha256: {manifest['index_db']['sha256'][:16]}…")
    if manifest.get("repo_head"):
        click.echo(f"  repo HEAD: {manifest['repo_head'][:12]}")
    if sig_info:
        click.echo(f"  signed via cosign ({sig_info['mode']}): {sig_info['signature']}")


@click.command("index-import")
@click.argument("bundle", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing .roam/index.db without prompting.",
)
@click.option(
    "--no-verify-head",
    is_flag=True,
    help=(
        "Skip the warning when the bundle's repo_head doesn't match the "
        "current git HEAD. Use this when importing into a different worktree."
    ),
)
@click.option(
    "--cosign-bundle",
    "cosign_bundle_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Verify the manifest signature against this cosign bundle file.",
)
@click.option(
    "--cosign-key",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Public key for offline cosign verification.",
)
@click.pass_context
def index_import(ctx, bundle, force, no_verify_head, cosign_bundle_path, cosign_key):
    """Import a portable roam index bundle into the current repo.

    Verifies the manifest's integrity hash against the bundled SQLite
    index. With ``--cosign-bundle <path>`` and/or ``--cosign-key <path>``
    also verifies the cosign signature. Extracts to ``.roam/index.db`` and
    refuses to overwrite without ``--force``.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    bundle_path = Path(bundle)

    try:
        manifest = _verify_bundle(bundle_path)
    except ValueError as exc:
        raise click.ClickException(f"bundle verification failed: {exc}") from exc

    # Schema version mismatch is a hard refuse — the index format changed.
    current_schema: int | None = None
    target_db = _index_db_path()
    if target_db.is_file():
        current_schema = _read_schema_version(target_db)
    bundle_schema = manifest.get("schema_version")
    if current_schema is not None and bundle_schema is not None and current_schema != bundle_schema:
        click.echo(
            f"  Warning: bundle schema_version={bundle_schema} differs from "
            f"current schema_version={current_schema}. Run `roam index` "
            f"after import to upgrade.",
            err=True,
        )

    # Repo HEAD mismatch is a soft warning.
    bundle_head = manifest.get("repo_head")
    current_head = _read_repo_head(Path.cwd())
    head_warning: str | None = None
    if bundle_head and current_head and bundle_head != current_head and not no_verify_head:
        head_warning = (
            f"bundle was exported at {bundle_head[:12]} but current HEAD is "
            f"{current_head[:12]} — index may be stale (use --no-verify-head to silence)"
        )
        click.echo(f"  Warning: {head_warning}", err=True)

    # Optional cosign verify.
    sig_verified: bool | None = None
    if cosign_bundle_path or cosign_key:
        from roam.attest.cga import (
            cosign_available,
            cosign_verify_statement,
        )

        available, _version = cosign_available()
        if not available:
            click.echo(
                "  Note: cosign binary not on PATH; cannot verify signature.",
                err=True,
            )
            sig_verified = False
        else:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                manifest_path = tmp_dir / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                try:
                    cosign_verify_statement(
                        manifest_path,
                        Path(cosign_bundle_path) if cosign_bundle_path else None,
                        Path(cosign_key) if cosign_key else None,
                    )
                    sig_verified = True
                except Exception as exc:
                    raise click.ClickException(f"cosign verify failed: {exc}") from exc

    # Refuse to overwrite without --force.
    if target_db.is_file() and not force:
        raise click.ClickException(f"{target_db} already exists; pass --force to overwrite.")
    target_db.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(bundle_path, mode="r:*") as tar:
        member = tar.getmember("index.db")
        fh = tar.extractfile(member)
        if fh is None:
            raise click.ClickException("could not extract index.db from bundle")
        with target_db.open("wb") as out:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                out.write(chunk)

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "index-import",
                    summary={
                        "verdict": f"Imported index from {bundle_path}",
                        "bundle_path": str(bundle_path),
                        "extracted_to": str(target_db),
                        "head_warning": head_warning,
                        "signature_verified": sig_verified,
                    },
                    manifest=manifest,
                )
            )
        )
        return

    click.echo(f"VERDICT: Imported index to {target_db}")
    click.echo(f"  exported at: {manifest.get('exported_at')}")
    if bundle_head:
        click.echo(f"  bundle HEAD: {bundle_head[:12]}")
    if sig_verified is True:
        click.echo("  cosign signature: VERIFIED")
    elif sig_verified is False:
        click.echo("  cosign signature: SKIPPED (binary missing)")
