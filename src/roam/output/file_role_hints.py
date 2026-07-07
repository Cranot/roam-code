"""Shared file-role classification hints used by headline-metric commands.

Several commands (``smells``, ``fan``, ``dead``, etc.) default-exclude
tooling/generated/example/vendor files from their headline output so
the user isn't dominated by code they can't or don't want to refactor.
This module centralises the path-hint set so all those commands stay
in sync — extracted in v12.4 from per-command duplicates.
``workspaces/`` (agent-generated
benchmark artifacts) as another category that polluted the headlines
on ``an agent-eval workspace``.

Hints are matched against the *path* (case-sensitive, both Unix and
Windows separators) by checking whether ``/<hint>/`` appears anywhere
in ``/<path>``. Each hint is a directory name with no leading or
trailing separator; the matcher adds them.
"""

from __future__ import annotations

# Categories of paths excluded from headline metrics by default.
# The matcher folds these into a single set; the categorisation here
# is documentary so future maintainers know *why* each entry is
# present and can disable a category without unrelated breakage.

_TOOLING_DIRS = (
    "dev",  # ``/dev/`` scripts & internal tooling
    "benchmarks",  # ``/benchmarks/`` perf scripts
    ".github",  # ``/.github/`` CI scripts and workflows
)

_GENERATED_DIRS = (
    "_generated",
    "_build",
    "generated",  # codegen output (protobuf stubs, openapi clients, ...)
    "build",  # setuptools / sphinx / similar build outputs
    "dist",
    "node_modules",
    "vendor",  # vendored third-party
    "third_party",
)

_EXAMPLE_DIRS = (
    "examples",
    "example",
    "workspaces",  # agent-eval / refactor-bench style: generated test artifacts
    "fixtures",  # often non-source — but conftest.py-style fixture *files*
    # are usually under tests/ and explicitly OK; the dir-name match is
    # for fixture-data dirs (``tests/fixtures/``).
    "samples",
)

_DOC_DIRS = (
    "docs",  # by convention not source code
    # F12: fastapi ships every tutorial as a standalone,
    # deliberately-untested teaching snippet under ``docs_src/``. Those 375
    # coverage-gap "violations" + 7/8 top dead-SAFE findings were all
    # ``docs_src/`` — one role-exclusion kills the whole false-positive class.
    # ``docs_src`` is the exact name the plain ``docs`` segment match missed
    # (``docs_src != docs``); ``doc_src`` / ``docs_source`` cover the common
    # spelling variants of the same convention.
    "docs_src",
    "doc_src",
    "docs_source",
)

_DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset((*_TOOLING_DIRS, *_GENERATED_DIRS, *_EXAMPLE_DIRS, *_DOC_DIRS))


def _split_dir_list(raw: object) -> frozenset[str]:
    """Parse a comma-separated directory-name list from config into a set."""
    if not raw or not isinstance(raw, str):
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def configured_role_exclusions(project_root=None) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(extra_exclude, re_include)`` from the ``[roles]`` config section.

    F12 config override. ``.roam/config.toml``::

        [roles]
        extra_exclude = "generated_ts, proto"   # add these dir names
        include = "examples, docs_src"           # re-admit these defaults

    Both keys are optional comma-separated directory-name lists (the simple
    scalar TOML the in-tree fallback parser supports). Missing file / section
    yields two empty sets — the default exclusions apply unchanged. Any config
    error degrades silently to "no override" so a detector never crashes on a
    malformed knob.
    """
    try:
        from roam.config import load_config

        roles = load_config(project_root).get("roles", {})
    except Exception:  # noqa: BLE001 — config is best-effort here
        return frozenset(), frozenset()
    return _split_dir_list(roles.get("extra_exclude")), _split_dir_list(roles.get("include"))


def is_excluded_path(
    path: str | None,
    *,
    extra_dirs: frozenset[str] | None = None,
    allow_dirs: frozenset[str] | None = None,
) -> bool:
    """Return True when ``path`` lives in a directory we exclude from
    headline metrics by default.

    Match is "any segment of the path equals one of the excluded
    directory names". Both Unix and Windows separators are normalised.
    Pass ``extra_dirs`` to add to the default set without redefining it;
    pass ``allow_dirs`` to re-admit specific defaults (the config
    ``[roles] include`` override) so a project that genuinely ships source
    under ``examples/`` can opt back in.

    W1029: ``path`` accepts ``None`` so callers can pass raw
    ``row["file_path"]`` without the cargo-cult ``or ""`` defensive
    wrapper. Returns ``False`` on ``None``/empty (a path we can't
    classify can't be excluded).
    """
    if not path:
        return False
    norm = path.replace("\\", "/")
    segments = {p for p in norm.split("/") if p}
    excluded = (_DEFAULT_EXCLUDED_DIRS | (extra_dirs or frozenset())) - (allow_dirs or frozenset())
    return bool(segments & excluded)


# Header markers that mark a file as machine-generated. When a file's
# first ~20 lines contain any of these phrases, it should be treated
# as generated regardless of where it lives. Typical for protobuf
# stubs, gRPC code, openapi clients, ANTLR parsers.
_GENERATED_HEADER_MARKERS: tuple[str, ...] = (
    "@generated",
    "this file is auto-generated",
    "this file is automatically generated",
    "do not edit",
    "do not modify",
    "code generated by",
    "auto-generated",
    "autogenerated",
    "generated by protoc",
    "generated by openapi",
    "generated by the following antlr",
    "this file was automatically generated",
)


def header_indicates_generated(text: str, *, scan_lines: int = 20) -> bool:
    """Return True when ``text``'s first ``scan_lines`` lines contain a
    generated-code marker (case-insensitive).
    """
    if not text:
        return False
    head = "\n".join(text.splitlines()[:scan_lines]).lower()
    return any(marker in head for marker in _GENERATED_HEADER_MARKERS)
