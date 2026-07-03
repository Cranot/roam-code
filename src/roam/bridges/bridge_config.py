"""Config string cross-language bridge: config files <-> code config reads.

Resolves cross-references between:
- Environment variable definitions in .env files and code that reads them
- YAML/JSON/TOML/INI config key definitions and code config lookups
- Settings objects (Django settings.X, process.env.X) and their definitions
"""

from __future__ import annotations

import os
import re

from roam.bridges.base import LanguageBridge
from roam.bridges.registry import register_bridge

# Config file extensions
_CONFIG_EXTS = frozenset({".env", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf"})

# Code file extensions that read config
_CODE_EXTS = frozenset({".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java", ".rb"})
_JS_LIKE_CODE_EXTS = frozenset({".js", ".ts", ".jsx", ".tsx"})

# --- Config key extraction patterns ---

# .env file: KEY=value
_ENV_KEY_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=", re.MULTILINE)

# YAML top-level keys: key: value (indentation 0)
_YAML_KEY_RE = re.compile(r"^([a-zA-Z_]\w*)\s*:", re.MULTILINE)

# --- Code config read patterns ---

# Python: os.environ.get('KEY'), os.environ['KEY'], os.getenv('KEY')
_PY_ENV_RE = re.compile(
    r"""os\s*\.\s*(?:environ\s*\.\s*get|environ\s*\[|getenv)\s*\(\s*['"]([\w]+)['"]""",
)

# Python: config['key'], config.get('key'), settings.KEY
_PY_CONFIG_RE = re.compile(
    r"""(?:config|settings|conf|cfg)\s*(?:\[['"](\w+)['"]\]|\.get\s*\(\s*['"](\w+)['"]|\.(\w+))""",
    re.IGNORECASE,
)

# JS/TS: process.env.KEY, process.env['KEY']
_JS_ENV_RE = re.compile(
    r"""process\s*\.\s*env\s*(?:\.(\w+)|\[\s*['"](\w+)['"]\s*\])""",
)

# JS/TS: config.get('key'), config.key, config['key']
_JS_CONFIG_RE = re.compile(
    r"""config\s*(?:\.get\s*\(\s*['"](\w[\w.]+)['"]|\.(\w+)|\[\s*['"](\w+)['"]\s*\])""",
    re.IGNORECASE,
)

# Go: os.Getenv("KEY"), viper.GetString("key")
_GO_ENV_RE = re.compile(
    r"""(?:os\.Getenv|viper\.Get\w*)\s*\(\s*["'](\w+)["']""",
)

# Java: System.getenv("KEY"), System.getProperty("key")
_JAVA_ENV_RE = re.compile(
    r"""System\s*\.\s*(?:getenv|getProperty)\s*\(\s*["'](\w+)["']""",
)


def _coalesce_alternative_key_capture(match: re.Match[str]) -> str | None:
    return next((group for group in match.groups() if group), None)


def _keys_from_alternative_read_pattern(pattern: re.Pattern[str], text: str) -> list[str]:
    return [key for match in pattern.finditer(text) if (key := _coalesce_alternative_key_capture(match))]


def _python_keys_from_config_read_syntax(text: str) -> list[str]:
    keys = [match.group(1) for match in _PY_ENV_RE.finditer(text)]
    keys.extend(_keys_from_alternative_read_pattern(_PY_CONFIG_RE, text))
    return keys


def _javascript_keys_from_config_read_syntax(text: str) -> list[str]:
    keys = _keys_from_alternative_read_pattern(_JS_ENV_RE, text)
    keys.extend(_keys_from_alternative_read_pattern(_JS_CONFIG_RE, text))
    return keys


def _config_keys_for_supported_read_syntax(text: str, ext: str) -> list[str]:
    if ext == ".py":
        return _python_keys_from_config_read_syntax(text)
    if ext in _JS_LIKE_CODE_EXTS:
        return _javascript_keys_from_config_read_syntax(text)
    if ext == ".go":
        return [match.group(1) for match in _GO_ENV_RE.finditer(text)]
    if ext == ".java":
        return [match.group(1) for match in _JAVA_ENV_RE.finditer(text)]
    return []


class ConfigBridge(LanguageBridge):
    """Bridge between config files and code that reads configuration."""

    @property
    def name(self) -> str:
        return "config"

    @property
    def source_extensions(self) -> frozenset[str]:
        return _CONFIG_EXTS

    @property
    def target_extensions(self) -> frozenset[str]:
        return _CODE_EXTS

    def detect(self, file_paths: list[str]) -> bool:
        """Detect if project has config files."""
        for fp in file_paths:
            ext = os.path.splitext(fp)[1].lower()
            basename = os.path.basename(fp).lower()
            if ext in _CONFIG_EXTS or basename == ".env":
                return True
        return False

    def resolve(self, source_path: str, source_symbols: list[dict], target_files: dict[str, list[dict]]) -> list[dict]:
        """Resolve config key definitions to code that reads them.

        Strategies:
        1. Environment variable matching: .env KEY=val -> os.environ.get('KEY')
        2. Config key matching: YAML key -> config['key'] or config.key
        """
        edges: list[dict] = []
        source_ext = os.path.splitext(source_path)[1].lower()
        source_basename = os.path.basename(source_path).lower()

        if source_ext not in _CONFIG_EXTS and source_basename != ".env":
            return edges

        # Extract config keys from source
        config_keys = self._extract_config_keys(source_symbols, source_ext, source_basename)

        if not config_keys:
            return edges

        source_file_label = os.path.basename(source_path)

        # Scan code files for config reads
        for tpath, tsymbols in target_files.items():
            text_ext = os.path.splitext(tpath)[1].lower()
            if text_ext not in _CODE_EXTS:
                continue

            code_keys = self._extract_code_config_reads(tsymbols, text_ext)

            # Match keys
            for config_key in config_keys:
                for code_key, sym_qname in code_keys:
                    if self._keys_match(config_key, code_key):
                        edges.append(
                            {
                                "source": f"{source_file_label}:{config_key}",
                                "target": sym_qname,
                                "kind": "x-lang",
                                "bridge": self.name,
                                "mechanism": "config-read",
                                "key": config_key,
                                "confidence": 0.85,
                            }
                        )

        return edges

    def _extract_config_keys(self, symbols: list[dict], ext: str, basename: str) -> set[str]:
        """Extract configuration key names from config file symbols."""
        keys: set[str] = set()
        for sym in symbols:
            name = sym.get("name", "")
            sig = sym.get("signature", "") or ""
            doc = sym.get("docstring", "") or ""
            text = f"{name} {sig} {doc}"

            if basename == ".env" or ext == ".env":
                keys.update(m.group(1) for m in _ENV_KEY_RE.finditer(text))
            if ext in (".yml", ".yaml"):
                keys.update(m.group(1) for m in _YAML_KEY_RE.finditer(text))

            # Also treat symbol names themselves as config keys
            # (the indexer extracts top-level keys as symbols in YAML/JSON)
            if name and not name.startswith("_"):
                keys.add(name)

        return keys

    def _extract_code_config_reads(self, symbols: list[dict], ext: str) -> list[tuple[str, str]]:
        """Extract config key reads from code symbols.

        Returns list of (key_name, symbol_qualified_name).
        """
        results: list[tuple[str, str]] = []
        for sym in symbols:
            qname = sym.get("qualified_name", sym.get("name", ""))
            sig = sym.get("signature", "") or ""
            doc = sym.get("docstring", "") or ""
            text = f"{sig} {doc}"

            for key in _config_keys_for_supported_read_syntax(text, ext):
                results.append((key, qname))

        return results

    def _keys_match(self, config_key: str, code_key: str) -> bool:
        """Check if a config file key matches a code config read.

        Supports:
        - Exact match: DATABASE_URL == DATABASE_URL
        - Case-insensitive match: database_url == DATABASE_URL
        - Dotted path match: database.host matches database
        """
        if config_key == code_key:
            return True
        if config_key.lower() == code_key.lower():
            return True
        # Dotted path: config_key might be a prefix
        if "." in code_key and code_key.split(".")[0].lower() == config_key.lower():
            return True
        return False


# Auto-register on import
register_bridge(ConfigBridge())
