"""Tests for A.0.5 — `.roam/config.toml` loader (`roam.config`)."""

from __future__ import annotations

from pathlib import Path

import click
import pytest

from roam.config import (
    DEFAULT_RETRIEVE_WEIGHTS,
    config_path,
    get_retrieve_config,
    get_retrieve_weights,
    load_config,
)


def _project(tmp_path: Path, contents: str | None = None) -> Path:
    """Create a project with a .roam/config.toml (or none)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()  # find_project_root looks for .git
    if contents is not None:
        roam_dir = proj / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.toml").write_text(contents, encoding="utf-8")
    return proj


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("ROAM_CONFIG", raising=False)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestConfigPath:
    def test_default_resolves_under_project_root(self, tmp_path):
        proj = _project(tmp_path)
        assert config_path(proj) == proj / ".roam" / "config.toml"

    def test_env_override_wins(self, tmp_path, monkeypatch):
        proj = _project(tmp_path)
        custom = tmp_path / "custom.toml"
        monkeypatch.setenv("ROAM_CONFIG", str(custom))
        assert config_path(proj) == custom


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        proj = _project(tmp_path)  # no config.toml
        cfg = load_config(proj)
        assert cfg["retrieve"]["alpha"] == DEFAULT_RETRIEVE_WEIGHTS["alpha"]
        assert cfg["retrieve"]["default_budget"] == 4000

    def test_empty_file_returns_defaults(self, tmp_path):
        proj = _project(tmp_path, "")
        cfg = load_config(proj)
        for key, value in DEFAULT_RETRIEVE_WEIGHTS.items():
            assert cfg["retrieve"][key] == value

    def test_partial_override_merges_with_defaults(self, tmp_path):
        proj = _project(
            tmp_path,
            "[retrieve]\nalpha = 0.55\n",
        )
        cfg = load_config(proj)
        assert cfg["retrieve"]["alpha"] == 0.55
        # untouched keys keep defaults
        assert cfg["retrieve"]["beta"] == DEFAULT_RETRIEVE_WEIGHTS["beta"]
        assert cfg["retrieve"]["default_rerank"] == "fast"

    def test_full_override_replaces_each_field(self, tmp_path):
        proj = _project(
            tmp_path,
            (
                "[retrieve]\n"
                "alpha = 0.5\n"
                "beta = 0.2\n"
                "gamma = 0.15\n"
                "delta = 0.1\n"
                "epsilon = 0.05\n"
                "default_budget = 8000\n"
                'default_rerank = "fast"\n'
            ),
        )
        cfg = load_config(proj)
        assert cfg["retrieve"]["alpha"] == 0.5
        assert cfg["retrieve"]["default_budget"] == 8000
        assert cfg["retrieve"]["default_rerank"] == "fast"

    def test_unknown_section_passes_through(self, tmp_path):
        """Foreign sections survive the merge — forward-compatible."""
        proj = _project(
            tmp_path,
            "[future_command]\nthing = 1\n",
        )
        cfg = load_config(proj)
        assert cfg["future_command"]["thing"] == 1
        # Defaults still present
        assert "retrieve" in cfg

    def test_malformed_toml_raises_clickexception(self, tmp_path):
        proj = _project(tmp_path, "this is = not = valid =\n")
        with pytest.raises(click.ClickException) as excinfo:
            load_config(proj)
        msg = str(excinfo.value.message)
        assert "config.toml" in msg or "parse" in msg.lower()

    def test_env_override_loads_from_custom_path(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom.toml"
        custom.write_text("[retrieve]\nalpha = 0.91\n", encoding="utf-8")
        monkeypatch.setenv("ROAM_CONFIG", str(custom))
        cfg = load_config()
        assert cfg["retrieve"]["alpha"] == 0.91


# ---------------------------------------------------------------------------
# Retrieve helpers
# ---------------------------------------------------------------------------


class TestRetrieveAccessors:
    def test_get_retrieve_weights_returns_five_floats(self, tmp_path):
        proj = _project(tmp_path)
        weights = get_retrieve_weights(proj)
        assert set(weights.keys()) == set(DEFAULT_RETRIEVE_WEIGHTS.keys())
        for value in weights.values():
            assert isinstance(value, float)

    def test_get_retrieve_weights_picks_up_overrides(self, tmp_path):
        proj = _project(tmp_path, "[retrieve]\nalpha = 0.7\nepsilon = 0.01\n")
        weights = get_retrieve_weights(proj)
        assert weights["alpha"] == 0.7
        assert weights["epsilon"] == 0.01
        assert weights["beta"] == DEFAULT_RETRIEVE_WEIGHTS["beta"]

    def test_get_retrieve_config_includes_non_weight_keys(self, tmp_path):
        proj = _project(
            tmp_path,
            "[retrieve]\ndefault_budget = 7777\n",
        )
        rcfg = get_retrieve_config(proj)
        assert rcfg["default_budget"] == 7777
        assert rcfg["alpha"] == DEFAULT_RETRIEVE_WEIGHTS["alpha"]


# ---------------------------------------------------------------------------
# Internal parser — exercise even when stdlib tomllib is available, by
# calling it directly so the fallback path is covered on every platform.
# ---------------------------------------------------------------------------


class TestSimpleTomlFallback:
    def test_basic_section(self):
        from roam.config import _parse_simple_toml

        result = _parse_simple_toml("[retrieve]\nalpha = 0.5\n")
        assert result == {"retrieve": {"alpha": 0.5}}

    def test_quoted_string(self):
        from roam.config import _parse_simple_toml

        result = _parse_simple_toml('[retrieve]\nname = "fast"\n')
        assert result == {"retrieve": {"name": "fast"}}

    def test_single_quoted_string(self):
        from roam.config import _parse_simple_toml

        result = _parse_simple_toml("[retrieve]\nname = 'fast'\n")
        assert result == {"retrieve": {"name": "fast"}}

    def test_boolean(self):
        from roam.config import _parse_simple_toml

        result = _parse_simple_toml("[retrieve]\non = true\noff = false\n")
        assert result == {"retrieve": {"on": True, "off": False}}

    def test_int_and_float(self):
        from roam.config import _parse_simple_toml

        result = _parse_simple_toml("[retrieve]\nbudget = 4000\nalpha = 0.4\n")
        assert result == {"retrieve": {"budget": 4000, "alpha": 0.4}}

    def test_inline_comment_after_value(self):
        from roam.config import _parse_simple_toml

        result = _parse_simple_toml("[retrieve]\nalpha = 0.5  # pagerank weight\n")
        assert result == {"retrieve": {"alpha": 0.5}}

    def test_blank_lines_and_full_comments(self):
        from roam.config import _parse_simple_toml

        text = """
        # top of file

        [retrieve]
        # weights
        alpha = 0.5

        beta = 0.25
        """
        result = _parse_simple_toml(text)
        assert result == {"retrieve": {"alpha": 0.5, "beta": 0.25}}

    def test_dotted_section_rejected(self):
        from roam.config import _parse_simple_toml

        with pytest.raises(ValueError, match="dotted"):
            _parse_simple_toml("[a.b]\nx = 1\n")

    def test_kv_before_section_rejected(self):
        from roam.config import _parse_simple_toml

        with pytest.raises(ValueError, match="before any"):
            _parse_simple_toml("alpha = 0.5\n")

    def test_missing_equals_rejected(self):
        from roam.config import _parse_simple_toml

        with pytest.raises(ValueError, match="missing"):
            _parse_simple_toml("[retrieve]\nalpha 0.5\n")

    def test_unparseable_value_rejected(self):
        from roam.config import _parse_simple_toml

        with pytest.raises(ValueError, match="cannot parse"):
            _parse_simple_toml("[retrieve]\nalpha = not_a_number\n")

    def test_quoted_value_with_hash_inside_preserved(self):
        from roam.config import _parse_simple_toml

        # Hash inside quotes is part of the string, not a comment marker.
        result = _parse_simple_toml('[retrieve]\nlabel = "fast # default"\n')
        assert result == {"retrieve": {"label": "fast # default"}}

    def test_array_value_rejected_by_fallback(self):
        """The minimal fallback parser doesn't support arrays — error must be clear."""
        from roam.config import _parse_simple_toml

        with pytest.raises(ValueError, match="cannot parse"):
            _parse_simple_toml("[retrieve]\nthings = [1, 2, 3]\n")


# ---------------------------------------------------------------------------
# ROAM_CONFIG env override edge cases
# ---------------------------------------------------------------------------


class TestRoamConfigEnvCases:
    def test_env_path_to_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        """When ROAM_CONFIG points at a file that does not exist, fall back
        to defaults silently — same behaviour as the project-relative path."""
        from roam.config import DEFAULT_RETRIEVE_WEIGHTS, load_config

        ghost = tmp_path / "ghost.toml"  # does not exist
        monkeypatch.setenv("ROAM_CONFIG", str(ghost))
        cfg = load_config()
        assert cfg["retrieve"]["alpha"] == DEFAULT_RETRIEVE_WEIGHTS["alpha"]

    def test_env_path_with_spaces(self, tmp_path, monkeypatch):
        """Path with spaces should round-trip through the env var."""
        from roam.config import load_config

        spacious = tmp_path / "with spaces" / "config.toml"
        spacious.parent.mkdir()
        spacious.write_text("[retrieve]\nalpha = 0.81\n", encoding="utf-8")
        monkeypatch.setenv("ROAM_CONFIG", str(spacious))
        assert load_config()["retrieve"]["alpha"] == 0.81
