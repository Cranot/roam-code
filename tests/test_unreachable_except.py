"""Tests for the precision-first unreachable-except detector."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_json_envelope, invoke_cli, parse_json_output


def _classify(symbol):
    from roam.db.connection import open_db
    from roam.world_model.unreachable_except import classify_unreachable_except

    with open_db(readonly=True) as conn:
        return classify_unreachable_except(conn, symbol_name=symbol)


def test_unreachable_except_flags_broader_handler_first(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def work():\n"
                "    try:\n"
                "        f()\n"
                "    except Exception:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        pass\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify("work")

    assert len(findings) == 1
    assert findings[0].shadowing_type == "Exception"
    assert findings[0].shadowed_type == "ValueError"
    assert findings[0].line_start == 6


def test_unreachable_except_reports_absolute_line_for_function_past_line_one(project_factory, monkeypatch):
    # Regression: the shadowed handler's reported line must be the ABSOLUTE file
    # line, not slice-relative. `work` starts at file line 8, so the shadowed
    # `except ValueError:` is at absolute line 13 (slice-relative line 6).
    proj = project_factory(
        {
            "src/example.py": (
                "import os\n"
                "\n"
                "\n"
                "def earlier():\n"
                "    return os.getcwd()\n"
                "\n"
                "\n"
                "def work():\n"
                "    try:\n"
                "        f()\n"
                "    except Exception:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        pass\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify("work")
    assert len(findings) == 1
    assert findings[0].shadowed_type == "ValueError"
    assert findings[0].line_start == 13


def test_unreachable_except_accepts_tuple_and_builtins_attribute(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def work():\n"
                "    try:\n"
                "        f()\n"
                "    except (builtins.Exception, KeyError):\n"
                "        pass\n"
                "    except builtins.ValueError:\n"
                "        pass\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify("work")
    assert len(findings) == 1
    assert findings[0].shadowing_type == "Exception"
    assert findings[0].shadowed_type == "ValueError"


def test_unreachable_except_stays_silent_for_ordered_unrelated_and_duplicate_handlers(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def ordered():\n"
                "    try:\n"
                "        f()\n"
                "    except ValueError:\n"
                "        pass\n"
                "    except Exception:\n"
                "        pass\n"
                "def unrelated():\n"
                "    try:\n"
                "        f()\n"
                "    except KeyError:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        pass\n"
                "def duplicate():\n"
                "    try:\n"
                "        f()\n"
                "    except ValueError:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        pass\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("ordered") == []
    assert _classify("unrelated") == []
    assert _classify("duplicate") == []


def test_unreachable_except_ignores_unknown_and_non_builtin_attribute(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "class CustomError(Exception):\n"
                "    pass\n"
                "def custom():\n"
                "    try:\n"
                "        f()\n"
                "    except CustomError:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        pass\n"
                "def external():\n"
                "    try:\n"
                "        f()\n"
                "    except requests.exceptions.ConnectionError:\n"
                "        pass\n"
                "    except ConnectionResetError:\n"
                "        pass\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("custom") == []
    assert _classify("external") == []


def test_unreachable_except_reports_first_shadowed_handler_and_one_per_symbol(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def work():\n"
                "    try:\n"
                "        f()\n"
                "    except Exception:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        pass\n"
                "    except TypeError:\n"
                "        pass\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify("work")
    assert len(findings) == 1
    assert findings[0].shadowed_type == "ValueError"
    assert findings[0].line_start == 6


def test_unreachable_except_skips_nested_scopes_and_async_functions(project_factory, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def outer():\n"
                "    def inner():\n"
                "        try:\n"
                "            f()\n"
                "        except Exception:\n"
                "            pass\n"
                "        except ValueError:\n"
                "            pass\n"
                "    return inner\n"
                "async def async_work():\n"
                "    try:\n"
                "        f()\n"
                "    except Exception:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        pass\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    assert _classify("outer") == []
    findings = _classify("async_work")
    assert len(findings) == 1
    assert findings[0].shadowed_type == "ValueError"


def test_unreachable_except_stays_silent_on_parse_failure(project_factory, monkeypatch):
    proj = project_factory({"src/example.py": "def work():\n    return 1\n"})
    monkeypatch.chdir(proj)
    (proj / "src/example.py").write_text("def work(:\n    return 1\n", encoding="utf-8")

    from roam.db.connection import open_db
    from roam.world_model.side_effects import SideEffectClassification
    from roam.world_model.unreachable_except import classify_unreachable_except

    with open_db(readonly=True) as conn:
        findings = classify_unreachable_except(
            conn,
            side_effects=[
                SideEffectClassification(
                    symbol="work",
                    file="src/example.py",
                    symbol_id=1,
                    line_start=1,
                    line_end=99,
                )
            ],
        )
    assert findings == []


def test_unreachable_except_is_opt_in_and_reachable_through_verify(project_factory, cli_runner, monkeypatch):
    proj = project_factory(
        {
            "src/example.py": (
                "def work():\n"
                "    try:\n"
                "        f()\n"
                "    except Exception:\n"
                "        pass\n"
                "    except ValueError:\n"
                "        pass\n"
            ),
        }
    )
    monkeypatch.chdir(proj)

    result = invoke_cli(
        cli_runner,
        ["verify", "--checks", "unreachable_except", "src/example.py"],
        cwd=proj,
        json_mode=True,
    )
    data = parse_json_output(result, "verify")
    assert_json_envelope(data, "verify")
    assert "unreachable_except" in data["summary"]["checks_run"]
    violations = data["categories"]["unreachable_except"]["violations"]
    assert len(violations) == 1

    from roam.commands.cmd_verify import _ALL_CHECKS, _DEFAULT_CHECKS

    assert "unreachable_except" in _ALL_CHECKS
    assert "unreachable_except" not in _DEFAULT_CHECKS
