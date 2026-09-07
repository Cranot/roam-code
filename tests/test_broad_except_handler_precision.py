"""Keep exception findings attached to the handler that supplies the evidence."""

from __future__ import annotations

import json
import textwrap

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.mark.parametrize(
    "source, expected_lines",
    [
        ("def run():\n    try: work()\n    except Exception: pass\n", [3]),
        ("def run():\n    try: work()\n    except Exception:\n        raise\n", []),
        ("def run():\n    try: work()\n    except Exception as exc:\n        return {'error': str(exc)}\n", []),
        (
            "class Worker:\n    def run(self):\n        try: work()\n        except Exception as exc:\n            return {'error': str(exc)}\n",
            [],
        ),
        ("def run():\n    try: work()\n    except Exception: pass\n    raise ValueError('later')\n", [3]),
        (
            "def run():\n    try: work()\n    except Exception: pass\n    try: other()\n    except Exception:\n        raise\n",
            [3],
        ),
        (
            "def run():\n    try: work()\n    except Exception: pass\n    try: other()\n    except Exception as exc:\n        errors.append(str(exc))\n",
            [3],
        ),
        ("def run():\n    try: work()\n    except Exception:\n        if retry: raise\n", [3]),
        ("def run():\n    try: work()\n    except Exception:\n        if ignored: return None\n        raise\n", [3]),
        (
            "def run():\n    try: work()\n    except Exception:\n        def delayed():\n            raise ValueError('later')\n        return None\n",
            [3],
        ),
        (
            "def run():\n    try: work()\n    except Exception as exc:\n        logger.warning('failed: %s', exc)\n        return None\n",
            [3],
        ),
    ],
)
def test_real_algo_handler_scope(project_factory, monkeypatch, source, expected_lines):
    root = project_factory({"worker.py": textwrap.dedent(source)})
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["--json", "algo", "--only", "detect_broad_except_swallow"])
    assert result.exit_code == 0, result.output
    findings = json.loads(result.stdout)["findings"]
    assert [int(row["location"].rsplit(":", 1)[1]) for row in findings] == expected_lines
    for row in findings:
        assert "silently swallows bugs" not in row["reason"]
        assert "handler" in row["reason"]


def test_conditional_cleanup_before_rethrow_is_not_recovery(project_factory, monkeypatch):
    root = project_factory(
        {
            "worker.py": (
                "def run():\n    try: work()\n    except BaseException:\n"
                "        if needs_cleanup: close_resource()\n        raise\n"
            )
        }
    )
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["--json", "algo", "--only", "detect_broad_except_swallow"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["findings"] == []
