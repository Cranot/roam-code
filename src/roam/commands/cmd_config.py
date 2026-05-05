"""Manage per-project roam configuration (.roam/config.json)."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import click

from roam.db.connection import (
    _load_project_config,
    db_exists,
    find_project_root,
    get_db_path,
    open_db,
    write_project_config,
)
from roam.output.formatter import json_envelope, to_json


def _validate_db_dir(db_dir: str) -> Path:
    """Ensure a DB override directory supports SQLite-style create/delete."""
    path = Path(db_dir).expanduser().resolve()
    probe = path / ".roam-db-dir-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise click.BadParameter(
            f"must be a writable directory with delete permission ({exc})",
            param_hint="--set-db-dir",
        ) from exc
    return path


def _emit_config_json(summary: dict, **payload) -> None:
    click.echo(to_json(json_envelope("config", summary=summary, **payload)))


def _exclude_list(config_data: dict) -> list:
    excludes = config_data.get("exclude", [])
    return list(excludes) if isinstance(excludes, list) else []


def _semantic_updates(semantic_backend, onnx_model, onnx_tokenizer, onnx_max_length) -> dict:
    updates = {}
    if semantic_backend is not None:
        updates["semantic_backend"] = semantic_backend.lower()
    if onnx_model is not None:
        updates["onnx_model_path"] = onnx_model
    if onnx_tokenizer is not None:
        updates["onnx_tokenizer_path"] = onnx_tokenizer
    if onnx_max_length is not None:
        updates["onnx_max_length"] = max(16, min(int(onnx_max_length), 1024))
    return updates


def _cache_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return slug or "project"


def _local_cache_base() -> Path:
    override = os.environ.get("ROAM_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base)
        return Path.home() / "AppData" / "Local"
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base)
    return Path.home() / ".cache"


def _project_local_cache_db_dir(root: Path) -> Path:
    resolved = root.resolve()
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    return _local_cache_base() / "roam-code" / "db" / f"{_cache_slug(resolved.name)}-{digest}"


def _save_db_dir(root: Path, db_dir: str, json_mode: bool) -> None:
    validated_db_dir = str(_validate_db_dir(db_dir))
    config_path = write_project_config({"db_dir": validated_db_dir}, root)
    if json_mode:
        _emit_config_json(
            {"verdict": "saved", "db_dir": validated_db_dir},
            config_path=str(config_path),
            db_dir=validated_db_dir,
        )
        return

    click.echo(f"Saved db_dir = {validated_db_dir!r}")
    click.echo(f"Config written to {config_path}")
    click.echo(f"DB will be stored at: {get_db_path(root)}")


def _save_local_cache_db_dir(root: Path, json_mode: bool) -> None:
    cache_dir = str(_validate_db_dir(str(_project_local_cache_db_dir(root))))
    config_path = write_project_config({"db_dir": cache_dir}, root)
    if json_mode:
        _emit_config_json(
            {"verdict": "saved-local-cache", "db_dir": cache_dir},
            config_path=str(config_path),
            db_dir=cache_dir,
            cache_strategy="per-project-local-cache",
        )
        return

    click.echo(f"Saved db_dir = {cache_dir!r}")
    click.echo(f"Config written to {config_path}")
    click.echo("DB will use the per-project local cache path.")


def _save_semantic_settings(root: Path, semantic_updates: dict, json_mode: bool) -> None:
    config_path = write_project_config(semantic_updates, root)
    if json_mode:
        _emit_config_json(
            {"verdict": "saved-semantic-settings"},
            config_path=str(config_path),
            **semantic_updates,
        )
        return

    click.echo("Saved semantic settings:")
    for k, v in semantic_updates.items():
        click.echo(f"  {k} = {v!r}")
    click.echo(f"Config written to {config_path}")
    click.echo("Re-run `roam index` to refresh semantic vectors with new settings.")


def _semantic_coverage_for_project(root: Path) -> dict:
    if not db_exists(root):
        return {
            "status": "no-index",
            "ready": False,
            "symbols": 0,
            "embeddings": 0,
            "onnx_embeddings": 0,
            "coverage_pct": 0.0,
        }

    from roam.retrieve.semantic import semantic_coverage

    try:
        with open_db(readonly=True, project_root=root) as conn:
            return semantic_coverage(conn)
    except Exception as exc:
        return {
            "status": "unreadable-index",
            "ready": False,
            "symbols": 0,
            "embeddings": 0,
            "onnx_embeddings": 0,
            "coverage_pct": 0.0,
            "error": str(exc),
        }


def _semantic_activation_status(root: Path) -> dict:
    from roam.search.onnx_embeddings import load_semantic_settings, onnx_dependencies_available, onnx_ready

    settings = load_semantic_settings(root)
    ready, reason, settings = onnx_ready(project_root=root, settings=settings)
    deps_ok, deps_reason = onnx_dependencies_available()
    coverage = _semantic_coverage_for_project(root)

    actions = []
    backend = str(settings.get("semantic_backend", "auto"))
    if backend == "tfidf":
        actions.append("roam config --semantic-backend onnx")
    if not settings.get("onnx_model_path") or not settings.get("onnx_tokenizer_path"):
        actions.append(
            "roam config --semantic-backend onnx --set-onnx-model <model.onnx> --set-onnx-tokenizer <tokenizer.json>"
        )
    if not deps_ok:
        actions.append('pip install "roam-code[semantic]"')
    if coverage.get("status") == "no-index":
        actions.append("roam index")
    elif ready and not coverage.get("ready"):
        actions.append("roam index")

    dense_active = bool(ready and coverage.get("ready"))
    return {
        "verdict": "semantic dense retrieval active" if dense_active else "semantic dense retrieval inactive",
        "dense_active": dense_active,
        "onnx_ready": ready,
        "onnx_reason": reason,
        "onnx_dependencies": {"available": deps_ok, "reason": deps_reason},
        "settings": settings,
        "coverage": coverage,
        "next_actions": list(dict.fromkeys(actions)),
    }


def _show_semantic_status(root: Path, json_mode: bool) -> None:
    status = _semantic_activation_status(root)
    if json_mode:
        _emit_config_json(
            {
                "verdict": status["verdict"],
                "dense_active": status["dense_active"],
                "onnx_ready": status["onnx_ready"],
                "coverage_pct": status["coverage"]["coverage_pct"],
            },
            semantic=status,
        )
        return

    click.echo(f"SEMANTIC: {status['verdict']}")
    click.echo(f"  backend: {status['settings']['semantic_backend']}")
    click.echo(f"  onnx: {status['onnx_reason']}")
    click.echo(
        "  coverage: "
        f"{status['coverage']['embeddings']}/{status['coverage']['symbols']} "
        f"({status['coverage']['coverage_pct']}%)"
    )
    if status["next_actions"]:
        click.echo("NEXT:")
        for action in status["next_actions"]:
            click.echo(f"  {action}")


def _add_exclude(root: Path, current: dict, exclude_pattern: str, json_mode: bool) -> None:
    existing_excludes = _exclude_list(current)
    if exclude_pattern not in existing_excludes:
        existing_excludes.append(exclude_pattern)
    config_path = write_project_config({"exclude": existing_excludes}, root)

    if json_mode:
        _emit_config_json(
            {"verdict": "exclude-added", "pattern": exclude_pattern},
            exclude=existing_excludes,
            config_path=str(config_path),
        )
        return

    click.echo(f"Added exclude pattern: {exclude_pattern!r}")
    click.echo(f"Active config excludes: {existing_excludes}")
    click.echo(f"Config written to {config_path}")
    click.echo("")
    click.echo("Note: re-run `roam index` to re-index without excluded files.")


def _emit_exclude_not_found(existing_excludes: list, remove_pattern: str, json_mode: bool) -> None:
    if json_mode:
        _emit_config_json(
            {"verdict": "not-found", "pattern": remove_pattern},
            exclude=existing_excludes,
        )
        return

    click.echo(f"Pattern {remove_pattern!r} not found in exclude list.")
    if existing_excludes:
        click.echo(f"Current excludes: {existing_excludes}")


def _remove_exclude(root: Path, current: dict, remove_pattern: str, json_mode: bool) -> None:
    existing_excludes = _exclude_list(current)
    if remove_pattern not in existing_excludes:
        _emit_exclude_not_found(existing_excludes, remove_pattern, json_mode)
        return

    existing_excludes.remove(remove_pattern)
    config_path = write_project_config({"exclude": existing_excludes}, root)
    if json_mode:
        _emit_config_json(
            {"verdict": "exclude-removed", "pattern": remove_pattern},
            exclude=existing_excludes,
            config_path=str(config_path),
        )
        return

    click.echo(f"Removed exclude pattern: {remove_pattern!r}")
    click.echo(f"Active config excludes: {existing_excludes}")
    click.echo(f"Config written to {config_path}")


def _exclude_state(root: Path, current: dict) -> tuple[list, list, list, list]:
    from roam.index.discovery import (
        BUILTIN_GENERATED_PATTERNS,
        _load_roamignore,
        load_exclude_patterns,
    )

    return (
        _load_roamignore(root),
        _exclude_list(current),
        list(BUILTIN_GENERATED_PATTERNS),
        load_exclude_patterns(root),
    )


def _emit_default_config_text(root: Path) -> None:
    click.echo("No .roam/config.json found (using defaults).")
    click.echo(f"Default DB path: {get_db_path(root)}")
    click.echo("")
    click.echo("Tip: if your project is on a network drive and you see")
    click.echo("  'sqlite3.OperationalError: invalid uri authority'")
    click.echo("  run:  roam config --set-db-dir <local-path>")


def _emit_config_values_text(root: Path, current: dict) -> None:
    click.echo(f"Config: {root / '.roam' / 'config.json'}")
    for k, v in current.items():
        if k != "exclude":
            click.echo(f"  {k} = {v!r}")
    click.echo(f"Resolved DB path: {get_db_path(root)}")


def _emit_patterns_text(label: str, patterns: list) -> None:
    if not patterns:
        click.echo(f"  {label}: (none)")
        return

    click.echo(f"  {label} ({len(patterns)} patterns):")
    for pattern in patterns:
        click.echo(f"    {pattern}")


def _emit_exclude_state_text(
    roamignore_patterns: list,
    config_excludes: list,
    builtin_patterns: list,
    all_patterns: list,
) -> None:
    click.echo("")
    click.echo("Exclude patterns:")
    _emit_patterns_text(".roamignore", roamignore_patterns)
    _emit_patterns_text("config.json", config_excludes)
    click.echo(f"  built-in ({len(builtin_patterns)} patterns):")
    for pattern in builtin_patterns:
        click.echo(f"    {pattern}")
    click.echo("")
    click.echo(f"Total active patterns: {len(all_patterns)}")
    click.echo("Content-based detection: files with '// Code generated' or '# Generated by' in first 3 lines")


_KNOWN_CONFIG_KEYS: dict[str, str] = {
    "db_dir": "Custom location for the SQLite index (network-drive workaround).",
    "semantic_backend": "Semantic search backend: tfidf | onnx | hybrid | auto.",
    "onnx_model": "Path to ONNX model file for semantic search.",
    "onnx_tokenizer": "Path to tokenizer.json matching the ONNX model.",
    "onnx_max_length": "Max token length for the ONNX encoder (16-1024).",
    "exclude": "Per-project glob patterns to exclude during indexing.",
    "thresholds": "Per-metric warning thresholds for `roam alerts`.",
    "delta_alerts": "Whether `roam alerts` emits delta-only output (boolean).",
    "default_k": "Default --k for `roam retrieve`.",
    "default_rerank": "Default --rerank for `roam retrieve` (fast | off | learned).",
    "default_budget": "Default token budget for `roam retrieve`.",
    "bench_hints": "Per-project critique bench-relevance overrides.",
}


def _validate_config(root: Path, current: dict, json_mode: bool) -> None:
    """redactedflag unknown keys / type mismatches in .roam/config.json."""
    issues: list[dict] = []
    for key in current.keys():
        if key not in _KNOWN_CONFIG_KEYS:
            issues.append(
                {
                    "key": key,
                    "kind": "unknown_key",
                    "hint": "not consumed anywhere in the codebase; check spelling.",
                }
            )

    bool_keys = {"delta_alerts"}
    int_keys = {"onnx_max_length", "default_k", "default_budget"}
    list_keys = {"exclude", "bench_hints"}
    dict_keys = {"thresholds"}
    str_keys = {"db_dir", "semantic_backend", "onnx_model", "onnx_tokenizer", "default_rerank"}

    for key, value in current.items():
        if key not in _KNOWN_CONFIG_KEYS:
            continue
        if key in bool_keys and not isinstance(value, bool):
            issues.append({"key": key, "kind": "type_mismatch", "hint": "expected boolean."})
        elif key in int_keys and not isinstance(value, int):
            issues.append({"key": key, "kind": "type_mismatch", "hint": "expected integer."})
        elif key in list_keys and not isinstance(value, list):
            issues.append({"key": key, "kind": "type_mismatch", "hint": "expected list."})
        elif key in dict_keys and not isinstance(value, dict):
            issues.append({"key": key, "kind": "type_mismatch", "hint": "expected object."})
        elif key in str_keys and value is not None and not isinstance(value, str):
            issues.append({"key": key, "kind": "type_mismatch", "hint": "expected string."})

    if not current:
        verdict = "no .roam/config.json — using defaults"
    elif not issues:
        verdict = f"OK — {len(current)} config key(s), all valid"
    else:
        verdict = f"{len(issues)} issue(s) in .roam/config.json"

    if json_mode:
        from roam.output.formatter import json_envelope, to_json

        click.echo(
            to_json(
                json_envelope(
                    "config",
                    summary={"verdict": verdict, "issues": len(issues)},
                    config=current,
                    issues=issues,
                    known_keys=_KNOWN_CONFIG_KEYS,
                )
            )
        )
        return

    click.echo(f"VERDICT: {verdict}")
    if issues:
        click.echo()
        click.echo(f"{'Key':<20}  {'Issue':<16}  Hint")
        click.echo(f"{'-' * 20}  {'-' * 16}  {'-' * 30}")
        for it in issues:
            click.echo(f"{it['key']:<20}  {it['kind']:<16}  {it['hint']}")
    if not issues:
        click.echo()
        click.echo("Known keys:")
        for k, descr in _KNOWN_CONFIG_KEYS.items():
            click.echo(f"  {k:<20}  {descr}")


def _show_rerank_weights(root: Path, json_mode: bool) -> None:
    """redactedprint the active rerank weights merged with defaults."""
    from roam.config import get_retrieve_weights

    weights = get_retrieve_weights(root)
    verdict = "rerank weights for this project"
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "config",
                    summary={"verdict": verdict, "weights": weights},
                    weights=weights,
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict}")
    click.echo()
    for k, v in weights.items():
        click.echo(f"  {k:<12}  {v:.4f}")


def _show_env_inventory(json_mode: bool) -> None:
    """redactedenumerate ROAM_* environment variables read by the codebase.

    Walks ``src/roam/`` looking for ``os.environ.get("ROAM_...")`` and
    ``os.environ["ROAM_..."]`` references. Result is sorted, deduped,
    and includes the file/line of the first read so users can find the
    spec for any variable.
    """
    import re
    from pathlib import Path as _Path

    pkg_root = _Path(__file__).resolve().parent.parent
    pattern = re.compile(r"""ROAM_[A-Z][A-Z0-9_]+""")

    env_index: dict[str, dict] = {}
    for py in pkg_root.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in pattern.finditer(text):
            name = m.group(0)
            if name in env_index:
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            try:
                rel_path = py.relative_to(pkg_root.parent).as_posix()
            except ValueError:
                rel_path = py.as_posix()
            env_index[name] = {
                "name": name,
                "file": rel_path,
                "line": line_no,
                "current": os.environ.get(name),
            }

    items = sorted(env_index.values(), key=lambda d: d["name"])
    if json_mode:
        from roam.output.formatter import json_envelope, to_json

        click.echo(
            to_json(
                json_envelope(
                    "config",
                    summary={
                        "verdict": f"{len(items)} ROAM_* env var(s) read by source",
                        "count": len(items),
                    },
                    env_vars=items,
                )
            )
        )
        return
    click.echo(f"VERDICT: {len(items)} ROAM_* env var(s) read by source")
    click.echo()
    click.echo(f"{'Name':<36}  {'Set?':<5}  First read")
    click.echo(f"{'-' * 36}  {'-' * 5}  {'-' * 40}")
    for it in items:
        is_set = "yes" if it["current"] is not None else "no"
        loc = f"{it['file']}:{it['line']}"
        click.echo(f"{it['name']:<36}  {is_set:<5}  {loc}")


def _show_config(root: Path, current: dict, json_mode: bool) -> None:
    roamignore_patterns, config_excludes, builtin_patterns, all_patterns = _exclude_state(root, current)
    if json_mode:
        payload = {
            **current,
            "exclude_roamignore": roamignore_patterns,
            "exclude_config": config_excludes,
            "exclude_builtin": builtin_patterns,
            "exclude_all": all_patterns,
        }
        _emit_config_json({"verdict": "ok"}, **payload)
        return

    if not current and not roamignore_patterns:
        _emit_default_config_text(root)
    else:
        _emit_config_values_text(root, current)
    _emit_exclude_state_text(roamignore_patterns, config_excludes, builtin_patterns, all_patterns)


def _no_mutating_options(
    db_dir,
    use_local_cache,
    semantic_backend,
    onnx_model,
    onnx_tokenizer,
    onnx_max_length,
    exclude_pattern,
    remove_pattern,
) -> bool:
    # redacted``use_local_cache`` is a ``is_flag=True`` Click option,
    # so its default is ``False`` not ``None``. The previous all(... is
    # None) check returned False even when nothing was passed, leaving
    # ``roam config`` (no flags, no --show) silent in --json mode.
    return (
        db_dir is None
        and not use_local_cache
        and semantic_backend is None
        and onnx_model is None
        and onnx_tokenizer is None
        and onnx_max_length is None
        and exclude_pattern is None
        and remove_pattern is None
    )


@click.command("config")
@click.option(
    "--set-db-dir",
    "db_dir",
    default=None,
    help="Redirect the index DB to this directory (useful for network drives).",
)
@click.option(
    "--use-local-cache",
    is_flag=True,
    help="Persist a deterministic per-project DB directory under the OS user cache.",
)
@click.option(
    "--semantic-backend",
    type=click.Choice(["auto", "tfidf", "onnx", "hybrid"], case_sensitive=False),
    default=None,
    help="Semantic search backend mode.",
)
@click.option(
    "--set-onnx-model",
    "onnx_model",
    default=None,
    help="Path to local ONNX model file for semantic search.",
)
@click.option(
    "--set-onnx-tokenizer",
    "onnx_tokenizer",
    default=None,
    help="Path to tokenizer.json matching the ONNX model.",
)
@click.option(
    "--set-onnx-max-length",
    "onnx_max_length",
    default=None,
    type=int,
    help="Max token length for ONNX encoder (16-1024).",
)
@click.option(
    "--exclude",
    "exclude_pattern",
    default=None,
    help="Add a glob pattern to the exclude list in .roam/config.json.",
)
@click.option(
    "--remove-exclude",
    "remove_pattern",
    default=None,
    help="Remove a glob pattern from the exclude list in .roam/config.json.",
)
@click.option("--show", is_flag=True, help="Print current configuration.")
@click.option("--semantic-status", is_flag=True, help="Show semantic backend readiness and embedding coverage.")
@click.option("--env", "env_inventory", is_flag=True, help="List every ROAM_* env var read by the codebase (Pass 36).")
@click.option(
    "--check", "validate_check", is_flag=True, help="Validate .roam/config.json keys against known schema (Pass 55)."
)
@click.option(
    "--weights",
    "show_weights",
    is_flag=True,
    help="redactedprint the active rerank weights (alpha/beta/gamma/delta/epsilon/zeta).",
)
@click.pass_context
def config(
    ctx,
    db_dir,
    use_local_cache,
    semantic_backend,
    onnx_model,
    onnx_tokenizer,
    onnx_max_length,
    exclude_pattern,
    remove_pattern,
    show,
    semantic_status,
    env_inventory,
    validate_check,
    show_weights,
):
    """Manage per-project roam configuration (.roam/config.json).

    Unlike ``doctor`` (which reads environment state for diagnostics), this command
    writes and manages the per-project .roam/config.json: DB location, ONNX semantic
    backend, and exclude patterns.

    Use ``--set-db-dir`` to redirect the SQLite database to a local directory
    when your project lives on a network drive or cloud-synced folder (OneDrive,
    Dropbox, etc.) where SQLite cannot open a read-only URI connection:

    \b
      # Windows network drive (M:)
      roam config --set-db-dir "C:\\\\Users\\\\you\\\\.roam-dbs\\\\myproject"
      # Or any local path
      roam config --set-db-dir /tmp/roam/myproject
      # Or a deterministic OS-local per-project cache path
      roam config --use-local-cache

    Use ``--exclude`` to add file exclusion patterns:

    \b
      roam config --exclude "*_pb2.py"
      roam config --exclude "generated/**"
      roam config --remove-exclude "*_pb2.py"

    Configure local ONNX semantic search:

    \b
      roam config --semantic-backend onnx
      roam config --set-onnx-model "./models/all-MiniLM-L6-v2.onnx"
      roam config --set-onnx-tokenizer "./models/tokenizer.json"
      roam config --set-onnx-max-length 256
      roam config --semantic-status

    The setting is saved to ``.roam/config.json`` and takes precedence over
    the default ``.roam/index.db`` location (but the ``ROAM_DB_DIR`` env-var
    still wins if set).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    current = _load_project_config(root)

    if db_dir is not None and use_local_cache:
        from roam.output.errors import INVALID_OPTIONS, structured_usage_error

        raise structured_usage_error(
            INVALID_OPTIONS,
            "choose either --set-db-dir or --use-local-cache, not both",
        )

    mutating_semantic = any(
        value is not None for value in (semantic_backend, onnx_model, onnx_tokenizer, onnx_max_length)
    )
    if semantic_status and (
        db_dir is not None
        or use_local_cache
        or exclude_pattern is not None
        or remove_pattern is not None
        or mutating_semantic
    ):
        from roam.output.errors import INVALID_OPTIONS, structured_usage_error

        raise structured_usage_error(
            INVALID_OPTIONS,
            "--semantic-status cannot be combined with config mutation options",
        )

    if db_dir is not None:
        _save_db_dir(root, db_dir, json_mode)
        return

    if use_local_cache:
        _save_local_cache_db_dir(root, json_mode)
        return

    if semantic_status:
        _show_semantic_status(root, json_mode)
        return

    if env_inventory:
        _show_env_inventory(json_mode)
        return

    if validate_check:
        _validate_config(root, current, json_mode)
        return

    if show_weights:
        _show_rerank_weights(root, json_mode)
        return

    semantic_updates = _semantic_updates(semantic_backend, onnx_model, onnx_tokenizer, onnx_max_length)
    if semantic_updates:
        _save_semantic_settings(root, semantic_updates, json_mode)
        return

    if exclude_pattern is not None:
        _add_exclude(root, current, exclude_pattern, json_mode)
        return

    if remove_pattern is not None:
        _remove_exclude(root, current, remove_pattern, json_mode)
        return

    if show or _no_mutating_options(
        db_dir,
        use_local_cache,
        semantic_backend,
        onnx_model,
        onnx_tokenizer,
        onnx_max_length,
        exclude_pattern,
        remove_pattern,
    ):
        _show_config(root, current, json_mode)
