"""Central first-use parser acquisition retry contract."""

from __future__ import annotations

from pathlib import Path

import pytest

import roam.parser_pack as parser_pack
from tests._helpers.repo_root import repo_root


def test_sealed_production_grammar_contract_is_sorted_closed_and_unique() -> None:
    grammars = parser_pack.SEALED_PRODUCTION_GRAMMARS
    assert grammars == tuple(sorted(set(grammars)))
    assert len(grammars) == 35
    assert {"python", "typescript", "tsx", "vue", "svelte"} <= set(grammars)


def test_operator_pinned_parser_cache_is_configured_without_reusing_xdg(monkeypatch, tmp_path) -> None:
    configured: list[str] = []
    cache = str(tmp_path / "reviewed" / "libs")
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_DIR", cache)
    monkeypatch.setattr(parser_pack, "_configured_cache_path", None)
    monkeypatch.setattr(parser_pack, "_sealed_inventory", None)
    monkeypatch.setattr(
        parser_pack._language_pack,
        "configure",
        lambda config: configured.append(config.cache_dir),
    )
    monkeypatch.setattr(parser_pack._language_pack, "cache_dir", lambda: cache)
    monkeypatch.setattr(parser_pack._language_pack, "has_language", lambda _grammar: True)

    assert parser_pack.has_language("python") is True
    assert configured == [cache]


@pytest.mark.parametrize(
    "value",
    ["relative/cache", str(Path.cwd() / "cache" / ".." / "escape"), ""],
)
def test_operator_pinned_parser_cache_rejects_noncanonical_paths(monkeypatch, value) -> None:
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_DIR", value)
    monkeypatch.setattr(parser_pack, "_configured_cache_path", None)
    monkeypatch.setattr(parser_pack, "_sealed_inventory", None)
    with pytest.raises(RuntimeError, match="canonical absolute path"):
        parser_pack.has_language("python")


def test_sealed_cache_miss_never_enters_acquisition_or_retry(monkeypatch, tmp_path) -> None:
    cache = str(tmp_path / "sealed" / "libs")
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_DIR", cache)
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_SEALED", "1")
    monkeypatch.setattr(parser_pack, "_configured_cache_path", None)
    monkeypatch.setattr(parser_pack, "_sealed_inventory", None)
    monkeypatch.setattr(parser_pack, "_sealed_cache_path", None)
    monkeypatch.setattr(parser_pack._language_pack, "configure", lambda _config: None)
    monkeypatch.setattr(parser_pack._language_pack, "cache_dir", lambda: cache)
    monkeypatch.setattr(parser_pack._language_pack, "downloaded_languages", lambda: ["python"])

    def must_not_acquire(_grammar):
        raise AssertionError("sealed miss reached the auto-downloading acquisition path")

    monkeypatch.setattr(parser_pack._language_pack, "get_parser", must_not_acquire)
    monkeypatch.setattr(parser_pack.time, "sleep", must_not_acquire)
    with pytest.raises(RuntimeError, match="missing required grammar: typescript"):
        parser_pack.get_parser("typescript")


def test_sealed_cache_hit_loads_once_without_download_retry(monkeypatch, tmp_path) -> None:
    cache = str(tmp_path / "sealed" / "libs")
    parser = object()
    calls: list[str] = []
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_DIR", cache)
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_SEALED", "1")
    monkeypatch.setattr(parser_pack, "_configured_cache_path", None)
    monkeypatch.setattr(parser_pack, "_sealed_inventory", None)
    monkeypatch.setattr(parser_pack, "_sealed_cache_path", None)
    monkeypatch.setattr(parser_pack._language_pack, "configure", lambda _config: None)
    monkeypatch.setattr(parser_pack._language_pack, "cache_dir", lambda: cache)
    monkeypatch.setattr(parser_pack._language_pack, "downloaded_languages", lambda: ["python"])
    monkeypatch.setattr(parser_pack._language_pack, "get_parser", lambda grammar: calls.append(grammar) or parser)
    monkeypatch.setattr(
        parser_pack.time,
        "sleep",
        lambda _delay: (_ for _ in ()).throw(AssertionError("sealed hit entered retry backoff")),
    )

    assert parser_pack.get_parser("python") is parser
    assert calls == ["python"]


def test_sealed_cache_revalidates_inventory_before_every_load(monkeypatch, tmp_path) -> None:
    cache = str(tmp_path / "sealed" / "libs")
    inventories = iter((["python"], []))
    calls: list[str] = []
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_DIR", cache)
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_SEALED", "1")
    monkeypatch.setattr(parser_pack, "_configured_cache_path", None)
    monkeypatch.setattr(parser_pack, "_sealed_inventory", None)
    monkeypatch.setattr(parser_pack, "_sealed_cache_path", None)
    monkeypatch.setattr(parser_pack._language_pack, "configure", lambda _config: None)
    monkeypatch.setattr(parser_pack._language_pack, "cache_dir", lambda: cache)
    monkeypatch.setattr(parser_pack._language_pack, "downloaded_languages", lambda: next(inventories))
    monkeypatch.setattr(parser_pack._language_pack, "get_parser", lambda grammar: calls.append(grammar) or object())

    parser_pack.get_parser("python")
    with pytest.raises(RuntimeError, match="missing required grammar: python"):
        parser_pack.get_parser("python")

    assert calls == ["python"]


def test_sealed_cache_cannot_be_disabled_or_redirected_in_process(monkeypatch, tmp_path) -> None:
    cache = str(tmp_path / "sealed" / "libs")
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_DIR", cache)
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_SEALED", "1")
    monkeypatch.setattr(parser_pack, "_configured_cache_path", None)
    monkeypatch.setattr(parser_pack, "_sealed_inventory", None)
    monkeypatch.setattr(parser_pack, "_sealed_cache_path", None)
    monkeypatch.setattr(parser_pack._language_pack, "configure", lambda _config: None)
    monkeypatch.setattr(parser_pack._language_pack, "cache_dir", lambda: cache)
    monkeypatch.setattr(parser_pack._language_pack, "downloaded_languages", lambda: ["python"])
    monkeypatch.setattr(parser_pack._language_pack, "get_parser", lambda _grammar: object())

    parser_pack.get_parser("python")
    monkeypatch.delenv("ROAM_TREE_SITTER_CACHE_SEALED")
    with pytest.raises(RuntimeError, match="cannot be disabled or redirected"):
        parser_pack.get_parser("python")
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_SEALED", "1")
    monkeypatch.setenv("ROAM_TREE_SITTER_CACHE_DIR", str(tmp_path / "other" / "libs"))
    with pytest.raises(RuntimeError, match="cannot be disabled or redirected"):
        parser_pack.get_parser("python")


def test_parser_download_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = object()
    calls = 0
    sleeps: list[float] = []

    def acquire(_grammar: str):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise parser_pack.DownloadError(f"transient download {calls}")
        return parser

    monkeypatch.setattr(parser_pack._language_pack, "get_parser", acquire)
    monkeypatch.setattr(parser_pack.time, "sleep", sleeps.append)

    assert parser_pack.get_parser("python") is parser
    assert calls == 3
    assert sleeps == [0.25, 1.0]


def test_exhausted_download_retry_preserves_final_error(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = [parser_pack.DownloadError(f"download {index}") for index in range(3)]
    sleeps: list[float] = []

    def acquire(_grammar: str):
        raise errors.pop(0)

    monkeypatch.setattr(parser_pack._language_pack, "get_parser", acquire)
    monkeypatch.setattr(parser_pack.time, "sleep", sleeps.append)

    with pytest.raises(parser_pack.DownloadError, match="download 2"):
        parser_pack.get_parser("python")

    assert errors == []
    assert sleeps == [0.25, 1.0]


@pytest.mark.parametrize(
    "error",
    [
        parser_pack._language_pack.ChecksumMismatchError("checksum mismatch"),
        parser_pack.LanguageNotFoundError("grammar unavailable"),
        RuntimeError("parser runtime failed"),
    ],
)
def test_non_download_failures_are_never_retried(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    calls = 0

    def acquire(_grammar: str):
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(parser_pack._language_pack, "get_parser", acquire)
    monkeypatch.setattr(
        parser_pack.time,
        "sleep",
        lambda _delay: pytest.fail("non-download failures must not sleep"),
    )

    with pytest.raises(type(error), match=str(error)):
        parser_pack.get_parser("python")

    assert calls == 1


def test_language_acquisition_uses_the_same_retry_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    language = object()
    calls = 0

    def acquire(_grammar: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise parser_pack.DownloadError("transient language download")
        return language

    monkeypatch.setattr(parser_pack._language_pack, "get_language", acquire)
    monkeypatch.setattr(parser_pack.time, "sleep", lambda _delay: None)

    assert parser_pack.get_language("python") is language
    assert calls == 2


def test_source_parser_consumers_use_the_central_retry_boundary() -> None:
    source_root = repo_root() / "src" / "roam"
    violations: list[Path] = []

    for path in source_root.rglob("*.py"):
        if path.name == "parser_pack.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "from tree_sitter_language_pack import get_parser" in text:
            violations.append(path.relative_to(source_root))
        if "from tree_sitter_language_pack import get_language" in text:
            violations.append(path.relative_to(source_root))

    assert violations == []
