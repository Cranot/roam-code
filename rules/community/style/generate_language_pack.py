#!/usr/bin/env python3
"""Generate language-scoped style rules for the community pack.

This script writes deterministic YAML rule files into:
  rules/community/style/language-pack/

Rule families per (language, scope):
- max symbol lines: 80, 120
- max params: 4, 6
- max file lines: 500
"""

from __future__ import annotations

from pathlib import Path


LANGUAGES = [
    ("python", "py"),
    ("javascript", "js"),
    ("typescript", "ts"),
    ("java", "java"),
    ("go", "go"),
    ("ruby", "rb"),
    ("php", "php"),
    ("csharp", "cs"),
    ("rust", "rs"),
    ("kotlin", "kt"),
    ("swift", "swift"),
]

SCOPES = [
    "src",
    "app",
    "lib",
    "services",
    "domain",
    "api",
    "internal",
]

MAX_SYMBOL_LINES = [80, 120]
MAX_PARAMS = [4, 6]
MAX_FILE_LINES = [500]


def _rule_header(name: str, description: str, severity: str = "info") -> str:
    return (
        f'name: "{name}"\n'
        f'description: "{description}"\n'
        f"severity: {severity}\n"
        "type: symbol_match\n\n"
        "match:\n"
    )


def _symbol_lines_rule(language: str, ext: str, scope: str, limit: int) -> tuple[str, str]:
    slug = f"style_{language}_{scope}_max_symbol_lines_{limit}"
    content = (
        _rule_header(
            name=f"Style: {language} {scope} max symbol lines {limit}",
            description=f"{language.title()} functions/methods in {scope}/ should stay under {limit} lines.",
        )
        + "  kind: [function, method]\n"
        + f'  file_glob: "{scope}/**/*.{ext}"\n'
        + "  require:\n"
        + f"    max_symbol_lines: {limit}\n"
    )
    return slug, content


def _max_params_rule(language: str, ext: str, scope: str, limit: int) -> tuple[str, str]:
    slug = f"style_{language}_{scope}_max_params_{limit}"
    content = (
        _rule_header(
            name=f"Style: {language} {scope} max params {limit}",
            description=f"{language.title()} functions/methods in {scope}/ should use at most {limit} parameters.",
        )
        + "  kind: [function, method]\n"
        + f'  file_glob: "{scope}/**/*.{ext}"\n'
        + "  require:\n"
        + f"    max_params: {limit}\n"
    )
    return slug, content


def _max_file_lines_rule(language: str, ext: str, scope: str, limit: int) -> tuple[str, str]:
    slug = f"style_{language}_{scope}_max_file_lines_{limit}"
    content = (
        _rule_header(
            name=f"Style: {language} {scope} max file lines {limit}",
            description=f"{language.title()} files in {scope}/ should stay under {limit} lines.",
        )
        + "  kind: [function, method, class]\n"
        + f'  file_glob: "{scope}/**/*.{ext}"\n'
        + "  require:\n"
        + f"    max_file_lines: {limit}\n"
    )
    return slug, content


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    out_dir = root / "rules" / "community" / "style" / "language-pack"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Deterministic regeneration: clear only generated files in language-pack.
    for existing in out_dir.glob("*.yaml"):
        existing.unlink()

    count = 0
    for language, ext in LANGUAGES:
        for scope in SCOPES:
            for limit in MAX_SYMBOL_LINES:
                slug, content = _symbol_lines_rule(language, ext, scope, limit)
                (out_dir / f"{slug}.yaml").write_text(content, encoding="utf-8")
                count += 1
            for limit in MAX_PARAMS:
                slug, content = _max_params_rule(language, ext, scope, limit)
                (out_dir / f"{slug}.yaml").write_text(content, encoding="utf-8")
                count += 1
            for limit in MAX_FILE_LINES:
                slug, content = _max_file_lines_rule(language, ext, scope, limit)
                (out_dir / f"{slug}.yaml").write_text(content, encoding="utf-8")
                count += 1

    readme = (
        "# Language-Scoped Style Pack\n\n"
        "Auto-generated via `rules/community/style/generate_language_pack.py`.\n\n"
        f"Generated rule files: {count}\n"
    )
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    print(f"Wrote {count} generated style rules to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
