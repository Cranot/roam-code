"""A failed move must restore completed writes and the interrupted write."""

import builtins
import os

import pytest

from roam.refactor import transforms


@pytest.mark.parametrize("target_exists", [False, True])
@pytest.mark.parametrize("fail_at", ["target.py", "source.py", "caller_b.py"])
def test_move_restores_all_files_after_partial_write(tmp_path, monkeypatch, target_exists, fail_at):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    contents = {
        "source.py": "def greet():\n    return 1\n\nKEEP = 2\n",
        "caller_a.py": "from source import greet\nresult = greet()\n",
        "caller_b.py": "from source import greet\nresult = greet()\n",
        "caller_c.py": "from source import greet\nresult = greet()\n",
    }
    if target_exists:
        contents["target.py"] = "EXISTING = 3\n"
    for name, content in contents.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    before = {name: (tmp_path / name).read_bytes() for name in contents}
    plan = transforms._MoveApplyPlan(
        source_file="source.py",
        source_lines=contents["source.py"].splitlines(),
        start_idx=0,
        end_idx=2,
        target_file="target.py",
        target_lines=contents.get("target.py", "").splitlines(),
        symbol_lines=["def greet():", "    return 1"],
        sym={"name": "greet"},
        old_module="source",
        new_module="target",
        referencing_files=["caller_a.py", "caller_b.py", "caller_c.py"],
    )

    real_open = builtins.open
    interrupted = []

    def fail_once_after_truncation(file, mode="r", *args, **kwargs):
        if os.fspath(file) == fail_at and mode == "w" and not interrupted:
            interrupted.append(fail_at)
            # Model a disk write that changes bytes before reporting failure.
            with real_open(file, mode, *args, **kwargs) as stream:
                stream.write("# incomplete write\n")
            raise OSError("injected partial write failure")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail_once_after_truncation)
    with pytest.raises(OSError):
        transforms._apply_move(plan)

    assert interrupted == [fail_at]
    after = {name: (tmp_path / name).read_bytes() for name in before}
    assert after == before, "failed move left modified source, target, or callers"
    if not target_exists:
        assert not (tmp_path / "target.py").exists(), "failed move left a new target"


@pytest.mark.parametrize("original", [b"def greet():\r\n    return 1\r\n", b"def greet():\n    return 1"])
@pytest.mark.parametrize("before_open", [False, True])
def test_rollback_preserves_bytes_and_does_not_rewrite_untouched_file(tmp_path, monkeypatch, original, before_open):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source.py"
    source.write_bytes(original)
    plan = transforms._MoveApplyPlan(
        "source.py",
        transforms._read_file("source.py"),
        0,
        2,
        "target.py",
        [],
        ["def greet():", "    return 1"],
        {"name": "greet"},
        "source",
        "target",
        [],
    )
    real_open = builtins.open
    attempted = []

    def fail(file, mode="r", *args, **kwargs):
        if os.fspath(file) == "source.py" and mode in ("w", "wb"):
            attempted.append(mode)
            if len(attempted) == 1:
                if not before_open:
                    with real_open(file, "wb") as stream:
                        stream.write(b"partial")
                raise OSError("write failed")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail)
    with pytest.raises(OSError):
        transforms._apply_move(plan)
    assert source.read_bytes() == original
    assert not (tmp_path / "target.py").exists()
    if before_open:
        assert attempted == ["w"], "rollback wrote a file whose write never began"


def test_move_reports_unrestored_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "source.py").write_text("def greet():\n    return 1\n")
    monkeypatch.setattr(
        transforms,
        "find_symbol",
        lambda *a: {
            "id": 1,
            "name": "greet",
            "file_path": "source.py",
            "line_start": 1,
            "line_end": 2,
        },
    )
    monkeypatch.setattr(transforms, "_find_files_referencing", lambda *a: [])
    real_open = builtins.open

    def fail(file, mode="r", *args, **kwargs):
        if os.fspath(file) == "source.py" and mode in ("w", "wb"):
            with real_open(file, "wb") as stream:
                stream.write(b"partial")
            raise OSError("disk unavailable")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail)
    result = transforms.move_symbol(None, "greet", "target.py", dry_run=False)
    assert result["isError"] is True
    assert "source.py" in result["error"]
    assert "rolled back" not in result["error"]
    assert not any("rolled back" in w for w in result["warnings"])
    assert result["unrestored_files"] == ["source.py"]
