"""W1018 tests for :func:`roam.commands._yaml_loader.load_yaml_with_warnings`.

Phase 1 of the YAML-loader consolidation (W1016 memo). The helper ships
UNUSED here — these tests exercise its contract in isolation before
Phase 2 (W1019) migrates the five clean-win callsites
(``finding_suppress`` x2, ``cmd_budget``, ``cmd_check_rules``,
``smells_suppress``).

Coverage taxonomy (mirrors the §4 contract):

1. Missing file -> ``None``, no warning.
2. Valid YAML / JSON -> parsed object, no warning.
3. Read error (OSError) -> empty container + warning.
4. Malformed YAML -> empty container + warning.
5. Non-dict root when dict expected -> empty container + warning.
6. ``allow_list_root=True`` accepts a list root.
7. No-PyYAML JSON fallback returns the parsed object.
8. No-PyYAML tiny-parser fallback returns the parsed object.
9. No-PyYAML, no tiny-parser, non-JSON content -> warning.
10. No-PyYAML tiny-parser returns empty -> warning.
11. ``schema_validator`` returns warnings -> empty container + warnings appended.
12. ``schema_validator`` returns ``[]`` -> parsed object, no warning.
13. ``schema_validator`` raises -> empty container + warning.
14. ``warnings_out=None`` stays byte-identical to silent-empty contract.
15. Empty file (``yaml.safe_load("")`` -> None) -> empty container, no warning.

Test discipline: every test that expects an empty-container fallback also
asserts the warning prefix shape ``{label}: '{path}': ...`` so a future
warning-text drift gets caught here, not at a callsite.
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from roam.commands._yaml_loader import (
    append_warning,
    load_yaml_with_warnings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _no_pyyaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the PyYAML import inside the helper to fail.

    The helper imports ``yaml`` lazily inside ``load_yaml_with_warnings``;
    we intercept the import at the builtins level so existing
    ``import yaml`` statements in already-imported modules stay live but
    a fresh import (the one inside the helper) raises ImportError.
    """
    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml":
            raise ImportError("forced for test: PyYAML unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    # Drop any cached top-level yaml import so the helper truly re-imports.
    monkeypatch.delitem(sys.modules, "yaml", raising=False)


# ---------------------------------------------------------------------------
# 1. Missing file
# ---------------------------------------------------------------------------


def test_missing_file_returns_none_no_warning(tmp_path: Path) -> None:
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        tmp_path / "does-not-exist.yml",
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result is None
    assert warnings == []


def test_missing_file_with_warnings_none_is_silent(tmp_path: Path) -> None:
    # Pre-Pattern-2 callers pass warnings_out=None and must not crash.
    result = load_yaml_with_warnings(tmp_path / "missing.yml")
    assert result is None


# ---------------------------------------------------------------------------
# 2. Valid YAML / JSON parse path
# ---------------------------------------------------------------------------


def test_valid_yaml_dict_returns_parsed(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "ok.yml",
        "rules:\n  - task_id: io-in-loop\n    path_glob: 'src/**/*.ts'\n",
    )
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="ignore-findings", warnings_out=warnings,
    )
    assert isinstance(result, dict)
    assert "rules" in result
    assert result["rules"][0]["task_id"] == "io-in-loop"
    assert warnings == []


def test_valid_json_loads_via_yaml(tmp_path: Path) -> None:
    # JSON is well-formed YAML 1.2; both code paths must produce the same dict.
    path = _write(tmp_path, "ok.json", json.dumps({"k": [1, 2, 3]}))
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
    )
    assert result == {"k": [1, 2, 3]}
    assert warnings == []


# ---------------------------------------------------------------------------
# 3. OSError on read
# ---------------------------------------------------------------------------


def test_read_oserror_warns_and_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write(tmp_path, "exists.yml", "k: v\n")

    def _boom(self: Path, *args: Any, **kwargs: Any) -> str:
        raise OSError("simulated permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 1
    assert warnings[0].startswith(f"cfg: {str(path)!r}: ")
    assert "could not read" in warnings[0]
    assert "simulated permission denied" in warnings[0]


# ---------------------------------------------------------------------------
# 4. Malformed YAML
# ---------------------------------------------------------------------------


def test_malformed_yaml_warns_and_returns_empty(tmp_path: Path) -> None:
    # Unclosed flow sequence — PyYAML raises ScannerError / YAMLError.
    path = _write(tmp_path, "broken.yml", "rules: [unterminated\n")
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 1
    assert "malformed YAML" in warnings[0]
    assert warnings[0].startswith(f"cfg: {str(path)!r}: ")


# ---------------------------------------------------------------------------
# 5. Non-dict root when dict expected
# ---------------------------------------------------------------------------


def test_list_root_rejected_when_dict_expected(tmp_path: Path) -> None:
    path = _write(tmp_path, "list.yml", "- a\n- b\n")
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 1
    assert "root is 'list'" in warnings[0]
    assert "expected a mapping" in warnings[0]


def test_scalar_root_rejected_when_dict_expected(tmp_path: Path) -> None:
    path = _write(tmp_path, "scalar.yml", "42\n")
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 1
    assert "root is 'int'" in warnings[0]


# ---------------------------------------------------------------------------
# 6. allow_list_root=True accepts list
# ---------------------------------------------------------------------------


def test_list_root_accepted_when_allowed(tmp_path: Path) -> None:
    path = _write(tmp_path, "list.yml", "- a\n- b\n")
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        allow_list_root=True,
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result == ["a", "b"]
    assert warnings == []


def test_scalar_root_rejected_even_when_list_allowed(tmp_path: Path) -> None:
    path = _write(tmp_path, "scalar.yml", "42\n")
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        allow_list_root=True,
        config_label="cfg",
        warnings_out=warnings,
    )
    # Empty container in list-root mode is [].
    assert result == []
    assert len(warnings) == 1
    assert "expected a mapping or a list" in warnings[0]


# ---------------------------------------------------------------------------
# 7-10. No-PyYAML fallback paths
# ---------------------------------------------------------------------------


def test_no_pyyaml_json_fallback_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_pyyaml(monkeypatch)
    path = _write(tmp_path, "ok.json", json.dumps({"k": "v"}))
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
    )
    assert result == {"k": "v"}
    assert warnings == []


def test_no_pyyaml_tiny_parser_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_pyyaml(monkeypatch)
    path = _write(tmp_path, "ok.yml", "rules:\n  - task_id: x\n")

    def _tiny(text: str) -> dict[str, Any]:
        # Return a recognisable parsed shape.
        return {"rules": [{"task_id": "x"}]}

    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        tiny_parser=_tiny,
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result == {"rules": [{"task_id": "x"}]}
    assert warnings == []


def test_no_pyyaml_no_tiny_parser_non_json_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_pyyaml(monkeypatch)
    path = _write(tmp_path, "yaml-only.yml", "rules:\n  - a\n")
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 1
    assert "PyYAML not installed" in warnings[0]
    assert "not valid JSON" in warnings[0]


def test_no_pyyaml_tiny_parser_returns_empty_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_pyyaml(monkeypatch)
    path = _write(tmp_path, "unrecognised.yml", "weird stuff that nothing parses\n")

    def _tiny(text: str) -> dict[str, Any]:
        return {}

    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        tiny_parser=_tiny,
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 1
    assert "could not extract any documented-shape entries" in warnings[0]


def test_no_pyyaml_tiny_parser_raises_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_pyyaml(monkeypatch)
    path = _write(tmp_path, "x.yml", "stuff\n")

    def _tiny(text: str) -> dict[str, Any]:
        raise RuntimeError("tiny-parser boom")

    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        tiny_parser=_tiny,
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 1
    assert "fallback parser failed" in warnings[0]
    assert "tiny-parser boom" in warnings[0]


# ---------------------------------------------------------------------------
# 11-13. schema_validator
# ---------------------------------------------------------------------------


def test_schema_validator_warnings_appended_returns_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "ok.yml", "k: v\n")

    def _validator(_: Any) -> list[str]:
        return [
            "cfg: '" + str(path) + "': rules[0] is 'str', expected a mapping.",
            "cfg: '" + str(path) + "': rules[1] missing `task_id`.",
        ]

    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        schema_validator=_validator,
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 2
    assert "rules[0] is 'str'" in warnings[0]
    assert "missing `task_id`" in warnings[1]


def test_schema_validator_empty_list_returns_parsed(tmp_path: Path) -> None:
    path = _write(tmp_path, "ok.yml", "k: v\n")

    calls: list[Any] = []

    def _validator(data: Any) -> list[str]:
        calls.append(data)
        return []

    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        schema_validator=_validator,
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result == {"k": "v"}
    assert warnings == []
    assert calls == [{"k": "v"}]


def test_schema_validator_raises_is_contained(tmp_path: Path) -> None:
    path = _write(tmp_path, "ok.yml", "k: v\n")

    def _validator(_: Any) -> list[str]:
        raise ValueError("validator blew up")

    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        schema_validator=_validator,
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result == {}
    assert len(warnings) == 1
    assert "schema validator raised ValueError" in warnings[0]
    assert "validator blew up" in warnings[0]


# ---------------------------------------------------------------------------
# 14. warnings_out=None is silent
# ---------------------------------------------------------------------------


def test_warnings_out_none_is_byte_identical_silent_empty(tmp_path: Path) -> None:
    # Every malformed-input path must produce the same empty container
    # WITHOUT raising when warnings_out is None — that's the pre-Pattern-2
    # contract every existing caller depends on.
    cases = [
        _write(tmp_path, "broken.yml", "rules: [unterminated\n"),
        _write(tmp_path, "list.yml", "- a\n- b\n"),
        _write(tmp_path, "scalar.yml", "42\n"),
    ]
    for path in cases:
        result = load_yaml_with_warnings(path, config_label="cfg")
        assert result == {}, f"silent fallback broken for {path.name}"


# ---------------------------------------------------------------------------
# 15. Empty file
# ---------------------------------------------------------------------------


def test_empty_file_returns_empty_container_no_warning(tmp_path: Path) -> None:
    # yaml.safe_load("") -> None; helper normalises to {}.
    path = _write(tmp_path, "empty.yml", "")
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path, config_label="cfg", warnings_out=warnings,
    )
    assert result == {}
    assert warnings == []


def test_empty_file_list_root_returns_empty_list(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.yml", "")
    warnings: list[str] = []
    result = load_yaml_with_warnings(
        path,
        allow_list_root=True,
        config_label="cfg",
        warnings_out=warnings,
    )
    assert result == []
    assert warnings == []


# ---------------------------------------------------------------------------
# Direct tests for append_warning
# ---------------------------------------------------------------------------


def test_append_warning_shape(tmp_path: Path) -> None:
    # Use a real tmp_path so the Path serialisation is platform-natural;
    # this keeps the assertion portable across POSIX and Windows (where
    # Path("/tmp/x.yml") -> "\\tmp\\x.yml" and gets repr-escaped further).
    target = tmp_path / "x.yml"
    bucket: list[str] = []
    append_warning(bucket, "ignore-findings", target, "body clause.")
    assert len(bucket) == 1
    assert bucket[0].startswith("ignore-findings: ")
    assert bucket[0].endswith(": body clause.")
    # The path str (whatever shape it takes on this OS) must appear,
    # quoted via Python repr (single-quoted, with any backslash escapes).
    assert repr(str(target)) in bucket[0]


def test_append_warning_none_bucket_is_noop() -> None:
    # Must not raise and must not produce side effects.
    append_warning(None, "cfg", Path("/tmp/x.yml"), "anything")


# ---------------------------------------------------------------------------
# W1035 — parse_error_label kwarg (relabels "malformed YAML" wording so JSON-
# shaped callsites can say "malformed JSON" without changing parser paths).
# ---------------------------------------------------------------------------


class TestParseErrorLabel:
    """W1035 contract tests for the ``parse_error_label`` kwarg.

    Default ``"YAML"`` must stay byte-identical to pre-W1035 callers
    (regression-guards the W706/W1009/W1018 contracts). JSON-shaped and
    arbitrary-string labels swap the noun inside the "malformed X" body
    without touching any other wording.
    """

    def test_json_label_swaps_noun(self, tmp_path: Path) -> None:
        # Unclosed flow sequence — PyYAML still raises (JSON-on-disk that's
        # malformed gets caught by the YAML parser); the label change is
        # what surfaces "JSON" to the agent reading the warning.
        path = _write(tmp_path, "broken.json", "{\"k\": [unterminated\n")
        warnings: list[str] = []
        result = load_yaml_with_warnings(
            path,
            config_label="per-finding-suppressions",
            parse_error_label="JSON",
            warnings_out=warnings,
        )
        assert result == {}
        assert len(warnings) == 1
        assert "malformed JSON" in warnings[0]
        assert "malformed YAML" not in warnings[0]

    def test_default_label_stays_yaml(self, tmp_path: Path) -> None:
        # Regression guard: pre-W1035 callers that never pass the kwarg
        # must produce byte-identical "malformed YAML" wording.
        path = _write(tmp_path, "broken.yml", "rules: [unterminated\n")
        warnings: list[str] = []
        result = load_yaml_with_warnings(
            path, config_label="cfg", warnings_out=warnings,
        )
        assert result == {}
        assert len(warnings) == 1
        assert "malformed YAML" in warnings[0]

    def test_arbitrary_label_is_forward_proof(self, tmp_path: Path) -> None:
        # Any non-empty string is accepted — no validation. Forward-proofs
        # the helper for TOML / future formats without a code change here.
        path = _write(tmp_path, "broken.toml", "rules: [unterminated\n")
        warnings: list[str] = []
        result = load_yaml_with_warnings(
            path,
            config_label="cfg",
            parse_error_label="TOML",
            warnings_out=warnings,
        )
        assert result == {}
        assert len(warnings) == 1
        assert "malformed TOML" in warnings[0]


# ---------------------------------------------------------------------------
# W1040 — force_tiny_parser kwarg (escape hatch so domain-permissive callsites
# can bypass PyYAML and use their tiny_parser as the SOLE engine; needed by
# smells_suppress where PyYAML's strict timestamp coercion short-circuits the
# empty-container fallback before the W994 validator runs).
# ---------------------------------------------------------------------------


class TestForceTinyParser:
    """W1040 contract tests for the ``force_tiny_parser`` kwarg.

    Default ``False`` must stay byte-identical to pre-W1040 callers — every
    other test in this file regression-guards that. ``True`` routes
    straight to ``tiny_parser`` and never touches PyYAML or strict JSON,
    so domain-aware permissive parsers see EVERY input (including the
    ``expires: 2026-13-01`` shape PyYAML would reject as a malformed
    timestamp). The boundary check raises ``ValueError`` when the kwarg is
    set without a tiny_parser, so the misuse is loud at call site.
    """

    def test_force_tiny_parser_routes_to_tiny_parser_not_pyyaml(
        self, tmp_path: Path,
    ) -> None:
        # PyYAML IS installed here (no _no_pyyaml monkeypatch). The kwarg
        # must still skip it entirely and fire the tiny_parser. Use a
        # sentinel that yaml.safe_load could parse (so we can prove the
        # tiny_parser was preferred, not used as a fallback).
        path = _write(
            tmp_path, "ok.yml", "suppressions:\n  - id: 'x'\n",
        )
        calls: list[str] = []

        def _tiny(text: str) -> dict[str, Any]:
            calls.append(text)
            return {"suppressions": [{"id": "tiny-was-here"}]}

        warnings: list[str] = []
        result = load_yaml_with_warnings(
            path,
            tiny_parser=_tiny,
            config_label="smells-suppress",
            warnings_out=warnings,
            force_tiny_parser=True,
        )
        # tiny_parser fired exactly once on the raw file text.
        assert len(calls) == 1
        assert "suppressions:" in calls[0]
        # The tiny_parser's value wins — proving PyYAML was bypassed (PyYAML
        # would have returned ``{"suppressions": [{"id": "x"}]}``).
        assert result == {"suppressions": [{"id": "tiny-was-here"}]}
        assert warnings == []

    def test_force_tiny_parser_raise_emits_warning_returns_empty(
        self, tmp_path: Path,
    ) -> None:
        # Same vocabulary as the ImportError fallback path so the warning
        # text stays single-sourced (one regex catches both engines).
        path = _write(tmp_path, "x.yml", "expires: 2026-13-01\n")

        def _tiny(text: str) -> dict[str, Any]:
            raise RuntimeError("domain parser refused this row")

        warnings: list[str] = []
        result = load_yaml_with_warnings(
            path,
            tiny_parser=_tiny,
            config_label="smells-suppress",
            warnings_out=warnings,
            force_tiny_parser=True,
        )
        assert result == {}
        assert len(warnings) == 1
        assert "fallback parser failed" in warnings[0]
        assert "domain parser refused this row" in warnings[0]

    def test_force_tiny_parser_without_tiny_parser_raises_value_error(
        self, tmp_path: Path,
    ) -> None:
        # Boundary check: the kwarg requires a tiny_parser. ValueError, not
        # a silent empty-container — misuse should be loud at the call site
        # so callers learn the contract immediately.
        path = _write(tmp_path, "x.yml", "k: v\n")
        with pytest.raises(ValueError) as exc_info:
            load_yaml_with_warnings(
                path,
                config_label="smells-suppress",
                force_tiny_parser=True,
            )
        msg = str(exc_info.value)
        assert "force_tiny_parser" in msg
        assert "tiny_parser" in msg
