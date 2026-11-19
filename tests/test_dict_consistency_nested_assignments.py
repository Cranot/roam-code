"""Module registry checks must ignore assignments in nested scopes."""

import json

import pytest
from click.testing import CliRunner


def _check(tmp_path, monkeypatch, source):
    # Keep formatter metadata and any optional side effects away from user data.
    monkeypatch.chdir(tmp_path)
    for variable in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(variable, str(tmp_path))
    from roam.commands.cmd_dict_consistency import dict_consistency

    path = tmp_path / "registries.py"
    path.write_text(source, encoding="utf-8")
    result = CliRunner().invoke(
        dict_consistency,
        [str(path), "--prefix", "_PROCEDURE", "--check-consistency"],
        obj={"json": True},
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.mark.parametrize("scope", ["def build():", "async def build():", "class Builder:"])
def test_nested_registries_do_not_replace_module_registries(tmp_path, monkeypatch, scope):
    keys = [f"procedure_{i:02d}" for i in range(10)]
    procedures = dict.fromkeys(keys)
    handlers = dict.fromkeys(keys[:-1])
    source = (
        f"_PROCEDURES = {procedures!r}\n"
        f"_PROCEDURE_HANDLERS: dict = {handlers!r}\n"
        f"{scope}\n"
        f"    _PROCEDURES = {dict.fromkeys(keys[:3])!r}\n"
        "    _PROCEDURE_LOCAL: dict = {'local_only': None}\n"
    )

    report = _check(tmp_path, monkeypatch, source)
    assert report["dicts"] == {
        "_PROCEDURES": keys,
        "_PROCEDURE_HANDLERS": keys[:-1],
    }
    assert report["consistency"]["per_dict_missing"] == {
        "_PROCEDURES": [],
        "_PROCEDURE_HANDLERS": [keys[-1]],
    }
    assert report["consistency"]["all_keys_union"] == keys
    assert report["consistency"]["is_consistent"] is False


def test_consistent_plain_and_annotated_module_registries(tmp_path, monkeypatch):
    report = _check(
        tmp_path,
        monkeypatch,
        "_PROCEDURES = {'alpha': 1, 'beta': 2}\n_PROCEDURE_HANDLERS: dict = {'beta': 3, 'alpha': 4}\n",
    )
    assert report["dicts"] == {
        "_PROCEDURES": ["alpha", "beta"],
        "_PROCEDURE_HANDLERS": ["alpha", "beta"],
    }
    assert report["consistency"]["per_dict_missing"] == {
        "_PROCEDURES": [],
        "_PROCEDURE_HANDLERS": [],
    }
    assert report["consistency"]["is_consistent"] is True


@pytest.mark.parametrize(
    "source",
    [
        "if True:\n    _PROCEDURES = {'alpha': 1}\nelse:\n    _PROCEDURE_HANDLERS = {'alpha': 2}\n",
        "try:\n    _PROCEDURES = {'alpha': 1}\nexcept Exception:\n    _PROCEDURE_HANDLERS = {'alpha': 2}\n",
        "with context():\n    for item in items:\n        _PROCEDURES = {'alpha': 1}\n_PROCEDURE_HANDLERS = {'alpha': 2}\n",
    ],
)
def test_module_control_flow_keeps_registry_assignments(tmp_path, monkeypatch, source):
    report = _check(tmp_path, monkeypatch, source + "def nested():\n    _PROCEDURES = {'local': 1}\n")
    assert report["dicts"] == {"_PROCEDURES": ["alpha"], "_PROCEDURE_HANDLERS": ["alpha"]}
    assert report["consistency"]["is_consistent"] is True
