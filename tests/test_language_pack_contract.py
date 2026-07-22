"""Pin the parser-cache safety floor and the public locality boundary."""

from __future__ import annotations

from tests._helpers.repo_root import repo_root

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT = repo_root()
LANGUAGE_PACK_SPEC = "tree-sitter-language-pack>=1.13.3,<1.14"
RELEASE_LOCK_PACKAGE = "tree-sitter-language-pack==1.13.3"
RELEASE_LINUX_WHEEL_SHA256 = "ebcd8fa9435ff956bd82eeea21f492fb2ccfefd9be909fe5cdebabb892c4b034"


def test_language_pack_dependency_keeps_cross_process_cache_fix() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert LANGUAGE_PACK_SPEC in project["dependencies"]

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    package = next(item for item in lock["package"] if item["name"] == "tree-sitter-language-pack")
    assert package["version"] == "1.13.3"

    roam = next(item for item in lock["package"] if item["name"] == "roam-code")
    requirement = next(
        item for item in roam["metadata"]["requires-dist"] if item["name"] == "tree-sitter-language-pack"
    )
    assert requirement["specifier"] == ">=1.13.3,<1.14"


def test_publish_wheelhouse_pins_the_runtime_language_pack() -> None:
    release_lock = (ROOT / ".github" / "release-tools.lock").read_text(encoding="utf-8")

    assert release_lock.count(RELEASE_LOCK_PACKAGE) == 2
    assert f"--hash=sha256:{RELEASE_LINUX_WHEEL_SHA256}" in release_lock
    assert "tree-sitter-language-pack==1.6.2" not in release_lock


def test_language_pack_exception_surface_is_not_a_builtin_error_contract() -> None:
    from tree_sitter_language_pack import (
        ChecksumMismatchError,
        DownloadError,
        Error,
        LanguageNotFoundError,
        has_language,
    )

    assert issubclass(LanguageNotFoundError, Error)
    assert issubclass(DownloadError, Error)
    assert issubclass(ChecksumMismatchError, Error)
    assert not issubclass(LanguageNotFoundError, LookupError)
    assert not issubclass(DownloadError, RuntimeError)
    assert not issubclass(ChecksumMismatchError, DownloadError)
    assert not issubclass(LanguageNotFoundError, DownloadError)
    assert callable(has_language)


def test_every_tree_sitter_language_passes_the_networkless_availability_probe() -> None:
    from tree_sitter_language_pack import has_language

    from roam.index.parser import EXTENSION_MAP, GRAMMAR_ALIASES, REGEX_ONLY_LANGUAGES

    languages = set(EXTENSION_MAP.values()) - set(REGEX_ONLY_LANGUAGES)
    grammar_pairs = sorted((language, GRAMMAR_ALIASES.get(language, language)) for language in languages)
    missing = [pair for pair in grammar_pairs if not has_language(pair[1])]

    assert missing == []


def test_public_locality_claims_disclose_cold_parser_acquisition() -> None:
    public_claim_files = (
        "README.md",
        "docs/fresh-install-smoke.md",
        "templates/distribution/landing-page/index.html",
        "templates/distribution/landing-page/trust.html",
        "templates/distribution/landing-page/security.html",
        "templates/distribution/landing-page/privacy.html",
        "templates/distribution/landing-page/docs/getting-started.html",
        "templates/distribution/landing-page/docs/canonical-demo.html",
        "templates/legal/security-procurement-packet.md",
        "templates/legal/dpa.md",
    )
    banned = (
        "metrics-push is the only outbound surface",
        "metrics-push` remains the only opt-in command",
        "metrics-push</code> remains the only opt-in command",
        "the only opt-in command that sends roam-generated data",
        "single outbound surface",
        "zero egress",
        "zero network egress by default",
        "zero network egress after",
        "once installed, no internet access is required",
        "no data crosses the network",
        "air-gapped repos work like cloud repos",
        "no source-code or telemetry egress",
        "no repository-content or telemetry egress",
        "no inbound network listener",
        "no diff, no source, no identifiers are uploaded anywhere",
    )

    for relative in public_claim_files:
        text = (ROOT / relative).read_text(encoding="utf-8").casefold()
        for claim in banned:
            assert claim not in text, f"{relative} hides the cold parser-cache network boundary: {claim!r}"

    readme = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
    assert "tree-sitter-language-pack" in readme
    assert "checksum-verified" in readme
    assert "docs/network-boundary.md" in readme
    assert "prewarm" in readme
