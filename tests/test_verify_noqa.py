"""verify's error_handling check respects ruff `# noqa: BLE001` / `# noqa: E722`
(and a bare `# noqa`), so a deliberately-acknowledged broad/bare except isn't
re-flagged. Source-of-truth = the author's EXISTING in-line annotation (line-shift
proof, no separate suppressions file needed). Fixes the auto-correct-loop noise where
14 intended `except Exception:  # noqa: BLE001` resilience sites kept re-surfacing.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_verify import (
    _BROAD_EXCEPT_RE,
    _has_noqa,
    _mask_py_strings_comments,
)


def test_mask_py_strings_comments_skips_string_except() -> None:
    src = (
        "x = 1\n"
        's = """\n'
        "except Exception:\n"  # line 3 — inside a string literal
        '"""\n'
        "# except Exception:\n"  # line 5 — inside a comment
        "try:\n"
        "    pass\n"
        "except Exception:\n"  # line 8 — REAL code
        "    raise\n"
    )
    masked = _mask_py_strings_comments(src)
    # length-preserving so finditer offsets still map to line numbers
    assert len(masked) == len(src)
    lines = [src[: m.start()].count("\n") + 1 for m in _BROAD_EXCEPT_RE.finditer(masked)]
    assert lines == [8], lines  # only the real except, not the string/comment ones


def test_mask_unparseable_source_returned_unchanged() -> None:
    bad = "def f(:\n    except Exception:\n"  # won't tokenize
    assert _mask_py_strings_comments(bad) == bad


def test_has_noqa_unit() -> None:
    assert _has_noqa("    except Exception:  # noqa: BLE001", ("BLE001",))
    assert _has_noqa("    except Exception:  # noqa", ("BLE001",))  # bare = all
    assert _has_noqa("    x = 1  # noqa: E501, BLE001", ("BLE001",))
    assert not _has_noqa("    x = 1  # noqa: F401", ("BLE001",))
    assert not _has_noqa("    except Exception: pass", ("BLE001",))
    assert not _has_noqa("", ("BLE001",))


_SRC = """from __future__ import annotations


def risky():
    return 1


def fn_a():
    try:
        return risky()
    except Exception:  # noqa: BLE001
        raise


def fn_b():
    try:
        return risky()
    except Exception:
        raise
"""


def test_error_handling_respects_noqa(tmp_path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir(exist_ok=True)  # isolate index root from any stray /tmp/.git
    (proj / "m.py").write_text(_SRC, encoding="utf-8")
    old = os.getcwd()
    try:
        os.chdir(str(proj))
        from roam.index.indexer import Indexer

        Indexer(project_root=proj).run(force=True, quiet=True, progress_bar=False)
        res = CliRunner().invoke(
            cli, ["--json", "verify", "m.py", "--checks", "error_handling"], env={"ROAM_COMPILE_VERIFY": "1"}
        )
    finally:
        os.chdir(old)
    d = json.loads(res.output[res.output.index("{") :])
    broad = [v for v in d["categories"]["error_handling"]["violations"] if "broad" in v.get("message", "")]
    # fn_a's broad-except carries `# noqa: BLE001` → skipped; only fn_b's is flagged.
    assert len(broad) == 1, broad
    fn_b_except_line = next(
        i + 1 for i, ln in enumerate(_SRC.split("\n")) if "except Exception:" in ln and "noqa" not in ln
    )
    assert broad[0]["line"] == fn_b_except_line, broad
