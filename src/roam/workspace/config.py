"""Workspace configuration: discovery, loading, validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WORKSPACE_CONFIG_NAME = ".roam-workspace.json"
WORKSPACE_DB_DIR = ".roam-workspace"
WORKSPACE_DB_NAME = "workspace.db"


def find_workspace_root(start: str = ".") -> Path | None:
    """Walk up from *start* looking for a .roam-workspace.json file.

    Returns the directory containing the config, or None.
    """
    current = Path(start).resolve()
    while current != current.parent:
        if (current / WORKSPACE_CONFIG_NAME).exists():
            return current
        current = current.parent
    return None


def load_workspace_config(root: Path) -> dict[str, Any]:
    """Read and validate .roam-workspace.json from *root*.

    Returns the parsed config dict.  Raises FileNotFoundError or
    ValueError on problems.
    """
    config_path = root / WORKSPACE_CONFIG_NAME
    if not config_path.exists():
        raise FileNotFoundError(f"No workspace config at {config_path}")
    text = config_path.read_text(encoding="utf-8")
    cfg = json.loads(text)
    _validate_config(cfg)
    return cfg


def save_workspace_config(root: Path, config: dict[str, Any]) -> Path:
    """Write *config* as .roam-workspace.json to *root*.

    Returns the path to the written file.
    """
    config_path = root / WORKSPACE_CONFIG_NAME
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return config_path


def get_repo_paths(config: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Resolve each repo entry to an absolute path and its index DB path.

    Returns a list of dicts with keys: name, path (absolute), role,
    db_path (absolute).
    """
    results = []
    for repo in config.get("repos", []):
        repo_path = (root / repo["path"]).resolve()
        db_path = repo_path / ".roam" / "index.db"
        results.append({
            "name": repo.get("name", repo_path.name),
            "path": repo_path,
            "role": repo.get("role", ""),
            "db_path": db_path,
        })
    return results


def get_workspace_db_path(root: Path) -> Path:
    """Return the path to the workspace overlay DB."""
    ws_dir = root / WORKSPACE_DB_DIR
    ws_dir.mkdir(exist_ok=True)
    return ws_dir / WORKSPACE_DB_NAME


def _validate_config(cfg: dict[str, Any]) -> None:
    """Raise ValueError if the config is structurally invalid."""
    if not isinstance(cfg, dict):
        raise ValueError("Workspace config must be a JSON object")
    if "workspace" not in cfg:
        raise ValueError("Missing 'workspace' key in config")
    if "repos" not in cfg or not isinstance(cfg["repos"], list):
        raise ValueError("Missing or invalid 'repos' key in config")
    for i, repo in enumerate(cfg["repos"]):
        if "path" not in repo:
            raise ValueError(f"Repo entry {i} missing 'path'")
