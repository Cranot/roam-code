"""Resolve one project-owned test environment without running a candidate."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_test_environment(root: Path, targets: list[str]) -> tuple[str, Path, str]:
    """Return interpreter, working directory and selection source for a batch.

    Discovery stops at the repository root and refuses nested repositories or
    an explicit but unusable environment. Different environments require separate
    invocations; file ordering must never silently choose one for all tests.
    Keep the executable path unresolved: resolving a POSIX venv Python symlink
    to its base interpreter would discard that virtual environment.
    """
    root = root.resolve()
    if not targets:
        raise ValueError("no impacted test files were supplied")
    environments: set[tuple[str, Path, str]] = set()
    for target in targets:
        file = (root / target).resolve()
        if not file.is_relative_to(root):
            raise ValueError("impacted test path is outside the intended repository")
        directories = []
        directory = file.parent
        while directory.is_relative_to(root):
            if directory != root and (directory / ".git").exists():
                raise ValueError("impacted tests cross a nested repository boundary")
            directories.append(directory)
            if directory == root:
                break
            directory = directory.parent
        selected = (sys.executable, root, "current_interpreter")
        for directory in directories:
            for name in (".venv", "venv"):
                environment = directory / name
                if not os.path.lexists(environment):
                    continue
                if not environment.is_dir() or not environment.resolve().is_relative_to(root):
                    raise ValueError("project test environment is not a directory inside the intended repository")
                interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                if not interpreter.is_file() or (os.name != "nt" and not os.access(interpreter, os.X_OK)):
                    raise ValueError("project test environment has no usable Python executable")
                selected = (str(interpreter), directory, "project_venv")
                break
            if selected[2] == "project_venv":
                break
        environments.add(selected)
    if len(environments) != 1:
        raise ValueError("multiple project test environments selected; run each project separately")
    return next(iter(environments))
