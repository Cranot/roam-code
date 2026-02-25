"""Roam: Codebase comprehension tool for AI coding assistants."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("roam-code")
except PackageNotFoundError:
    __version__ = "dev"
