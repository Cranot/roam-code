"""verify's duplicate + silent-swallow detectors must not re-flag the three
false-positive classes that dominated the roam-code self-index (456 dup + 40
narrow-swallow FPs, 2026-06-05):

  * interface/ABC overrides -- a fn name defined in >=3 same-role files is a
    shared contract (every `*_lang.py` overrides `language_name`), not a copy.
  * substring naming variants -- `run_agent` ⊂ `run_agent_opt` is intentional.
  * cross-role mirrors -- a source fn and its `test_*` namesake are expected.
  * narrow-type silent swallows -- `except OSError: pass` cleanup is deliberate;
    only BROAD/bare `except: pass` is the dangerous swallow.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_verify import _SILENT_EXCEPT_RE


def _verify_json(proj, *args):
    runner = CliRunner()
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        res = runner.invoke(cli, ["--json", "verify", *args], env={"ROAM_COMPILE_VERIFY": "1"})
    finally:
        os.chdir(old)
    out = res.output
    return json.loads(out[out.index("{") :])


def _index(proj):
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.index.indexer import Indexer

        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
    finally:
        os.chdir(old)


def test_silent_swallow_regex_broad_only():
    R = _SILENT_EXCEPT_RE
    assert R.search("except:\n    pass")
    assert R.search("except Exception:\n    pass")
    assert R.search("except Exception as e:\n    pass")
    assert R.search("except BaseException:\n    ...")
    # narrow, specific types are deliberate control flow -> not matched
    assert not R.search("except OSError:\n    pass")
    assert not R.search("except ValueError:\n    pass")
    assert not R.search("except (OSError, KeyError):\n    pass")
    assert not R.search("except sqlite3.OperationalError:\n    pass")


def test_interface_contract_not_flagged_as_duplicate(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    hdr = "from __future__ import annotations\n\n\n"
    # 3 source files each defining the same interface method -> contract.
    for i in range(3):
        (proj / f"lang_{i}.py").write_text(hdr + "def language_name():\n    return 'x'\n", encoding="utf-8")
    _index(proj)
    env = _verify_json(proj, "lang_0.py", "--checks", "duplicates")
    msgs = [v["message"] for v in env["categories"]["duplicates"]["violations"]]
    assert not any("language_name" in m for m in msgs), msgs


def test_two_file_copy_still_flagged(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    hdr = "from __future__ import annotations\n\n\n"
    body = "def parse_widget_config():\n    return 1\n"
    (proj / "a.py").write_text(hdr + body, encoding="utf-8")
    (proj / "b.py").write_text(hdr + body, encoding="utf-8")
    _index(proj)
    env = _verify_json(proj, "a.py", "--checks", "duplicates")
    msgs = [v["message"] for v in env["categories"]["duplicates"]["violations"]]
    # 2 same-role files (< the 3-file contract threshold) -> real duplicate.
    assert any("parse_widget_config" in m for m in msgs), msgs


def test_load_rules_module_entrypoints_not_flagged(tmp_path):
    proj = tmp_path / "proj"
    (proj / "src" / "roam" / "rules").mkdir(parents=True)
    (proj / "src" / "roam" / "security").mkdir(parents=True)
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    hdr = "from __future__ import annotations\n\n\n"
    (proj / "src" / "roam" / "rules" / "engine.py").write_text(
        hdr + "def load_rules(path):\n    return []\n",
        encoding="utf-8",
    )
    (proj / "src" / "roam" / "security" / "taint_engine.py").write_text(
        hdr + "def load_rules(path):\n    return []\n",
        encoding="utf-8",
    )
    _index(proj)
    env = _verify_json(proj, "src/roam/rules/engine.py", "--checks", "duplicates")
    msgs = [v["message"] for v in env["categories"]["duplicates"]["violations"]]
    assert not any("load_rules" in m for m in msgs), msgs


def test_verify_module_entrypoints_not_flagged(tmp_path):
    proj = tmp_path / "proj"
    (proj / "src" / "roam" / "commands").mkdir(parents=True)
    (proj / "src" / "roam").mkdir(parents=True, exist_ok=True)
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    hdr = "from __future__ import annotations\n\n\n"
    (proj / "src" / "roam" / "commands" / "cmd_verify.py").write_text(
        hdr + "def verify():\n    return None\n",
        encoding="utf-8",
    )
    (proj / "src" / "roam" / "mcp_server.py").write_text(
        hdr + "def verify():\n    return None\n",
        encoding="utf-8",
    )
    _index(proj)
    env = _verify_json(proj, "src/roam/commands/cmd_verify.py", "--checks", "duplicates")
    msgs = [v["message"] for v in env["categories"]["duplicates"]["violations"]]
    assert not any("`verify`" in m for m in msgs), msgs


def test_public_name_not_compared_to_private_helper(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    hdr = "from __future__ import annotations\n\n\n"
    (proj / "user_config.py").write_text(hdr + "def _load_user_config():\n    return {}\n", encoding="utf-8")
    (proj / "verify_config.py").write_text(hdr + "def load_verify_config():\n    return {}\n", encoding="utf-8")
    _index(proj)
    env = _verify_json(proj, "verify_config.py", "--checks", "duplicates")
    msgs = [v["message"] for v in env["categories"]["duplicates"]["violations"]]
    assert not any("load_verify_config" in m for m in msgs), msgs


def test_cross_role_mirror_not_flagged(tmp_path):
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "tests").mkdir(parents=True)
    hdr = "from __future__ import annotations\n\n\n"
    (proj / "src" / "mod.py").write_text(hdr + "def compute_widget_score():\n    return 1\n", encoding="utf-8")
    (proj / "tests" / "test_mod.py").write_text(hdr + "def compute_widget_score():\n    return 1\n", encoding="utf-8")
    _index(proj)
    env = _verify_json(proj, "src/mod.py", "--checks", "duplicates")
    msgs = [v["message"] for v in env["categories"]["duplicates"]["violations"]]
    # source fn vs its test-role namesake -> expected mirror, not duplication.
    assert not any("compute_widget_score" in m for m in msgs), msgs
