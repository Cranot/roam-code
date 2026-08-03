"""W1459 — a cap that stops the analysis must stop the CLEAN verdict too.

THE DEFECT
----------
W1455 made ``scripts/scan_disclosure_asymmetry.py`` fail closed on the
OUTERMOST early exit: a file it could not read or parse became
``UNANALYZABLE`` instead of an empty finding list. It did not cover the early
exits INSIDE the analysis, and there were three, each of the same shape it
had just removed:

* ``_expand(..., limit=4000)`` — stop the callee closure, return what it had;
* ``_MAX_DEPTH = 12`` — stop the token walk, return what it had;
* ``for _ in range(3)`` in ``_carried_tokens`` — stop the return-value
  fixpoint mid-flight, return what it had.

None of the three said so. The file they truncated was reported ``CLEAN``,
counted in ``files_parsed``, and exited 0. The published denominator counted
FILES; it did not count COMPLETENESS OF THE ANALYSIS, so a file examined to
depth 12 was byte-identical in the report to one examined to the end — which
is exactly the equivalence W1455 broke one level up, reintroduced one level
down. Measured at HEAD, the token walk was reaching depth 10 of its 12.

The second instance, same shape, in ``roam secrets``::

    files_scanned += 1
    file_findings = scan_file(str(full_path), min_severity=min_severity)

``scan_file`` swallows ``OSError`` and returns ``[]``, so a file that could
not be opened still incremented the number the verdict quotes: "No secrets
found (N files scanned)" over a set including files nothing ever decoded.
The defect "roam secrets certified files it never opened" had been fixed in
the VERDICT and left standing in the COUNTER.

WHAT IS PINNED HERE
-------------------
1. Reaching any Class-A cap means the file is not ``CLEAN`` — it is
   ``PARTIAL`` or, where the fix removed the cap, a violation that was
   previously invisible.
2. The cap counts are published (``cap_hits`` / ``files_capped``) and the
   CLI exit code is non-zero when any fired.
3. ``roam secrets`` counts files it READ. A file it could not read is in
   ``files_unreadable``, never in ``files_scanned``.
4. NEGATIVE CONTROL: a normal file still comes back ``CLEAN`` at exit 0, and
   a readable project still counts every file as scanned — so "mark
   everything partial" cannot satisfy 1-3.

PRE-FIX BEHAVIOUR, MEASURED
---------------------------
``git show HEAD:scripts/scan_disclosure_asymmetry.py`` run against the two
planted corpora below returns ``[]`` for both — the deep-helper module and
the deep-return-chain module are reported clean, with the asymmetry inside
them undetected. Post-fix both are found. See
``test_prefix_scanner_reports_these_corpora_clean``.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
SCANNER = REPO_ROOT / "scripts" / "scan_disclosure_asymmetry.py"

_SCRIPTS = str(REPO_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import scan_disclosure_asymmetry as disclosure  # noqa: E402

# ---------------------------------------------------------------------------
# Planted corpora
# ---------------------------------------------------------------------------

#: A genuinely symmetric command — the negative-control arm.
SYMMETRIC_COMMAND = """
import click


@click.command()
@click.pass_context
def probe(ctx):
    warnings_out = []
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))
    else:
        for marker in warnings_out:
            click.echo(f"# warning: {marker}", err=True)
        click.echo("VERDICT: ok")
"""


def deep_helper_command(links: int = 20) -> str:
    """A json-only disclosure parked behind ``links`` helper calls.

    The token appears at NEITHER the dispatch nor the call site — only in the
    emitter at the end of the chain. Pre-fix, the walk stopped at depth 12 and
    the json branch was recorded as observing nothing, so the asymmetry was
    invisible and the module reported clean.
    """
    chain = "\n\n".join(f"def _h{i}():\n    _h{i + 1}()" for i in range(links))
    tail = f'def _h{links}():\n    click.echo(to_json({{"warnings_out": []}}))'
    return f"""
import click


@click.command()
@click.pass_context
def deep_probe(ctx):
    json_mode = ctx.obj.get("json")
    if json_mode:
        _h0()
    else:
        click.echo("VERDICT: ok")


{chain}


{tail}
"""


def deep_return_chain_command(links: int = 8) -> str:
    """A json-only disclosure carried back through ``links`` RETURN hops.

    The bucket is produced in SHARED code and used in one branch only, which
    is the shape ``_carried_tokens`` exists for: the mode walk attributes the
    chain to both modes (so it credits neither), and the only thing that can
    tell the json branch what ``bucket`` holds is the return-value fixpoint.

    That fixpoint propagates one chain edge per round in source iteration
    order, so with the old ``for _ in range(3)`` a chain of eight returns
    never reached its head. ``_r0`` was recorded as carrying nothing, the
    json branch's alias resolved to nothing, and the module reported clean —
    with ``changed`` still true when the loop gave up.
    """
    chain = "\n\n".join(f"def _r{i}():\n    return _r{i + 1}()" for i in range(links))
    tail = f'def _r{links}():\n    return {{"warnings_out": []}}'
    return f"""
import click


@click.command()
@click.pass_context
def chain_probe(ctx):
    bucket = _r0()
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json({{"data": bucket}}))
    else:
        click.echo("VERDICT: ok")


{chain}


{tail}
"""


def wide_closure_command(links: int = 12) -> str:
    """A command whose in-module callee closure is ``links`` names deep.

    Used with a scaled-down ``_EXPAND_LIMIT`` so the closure cap fires
    without generating a 4000-function module. The mechanism under test is
    "the walk stopped early and the file did not report clean"; the number
    that stopped it is not the subject.
    """
    chain = "\n\n".join(f"def _c{i}():\n    _c{i + 1}()" for i in range(links))
    tail = f'def _c{links}():\n    click.echo(to_json({{"warnings_out": []}}))'
    return f"""
import click


@click.command()
@click.pass_context
def wide_probe(ctx):
    json_mode = ctx.obj.get("json")
    if json_mode:
        _c0()
    else:
        click.echo("VERDICT: ok")


{chain}


{tail}
"""


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# 1. Each cap, planted — the file must not come back clean
# ---------------------------------------------------------------------------


def test_depth_cap_no_longer_hides_a_deep_disclosure(tmp_path: pathlib.Path) -> None:
    """The ``_MAX_DEPTH = 12`` instance.

    Pre-fix: the walk returned at depth 13 and the module reported CLEAN.
    Post-fix the cap is gone — replaced by an explicit worklist whose
    termination comes from the ``visited`` set — so the disclosure 20 helpers
    down is FOUND. Not clean, either way round.
    """
    target = tmp_path / "cmd_deep.py"
    target.write_text(deep_helper_command(links=20), encoding="utf-8")

    result = disclosure.scan_file(target)

    assert result.status != disclosure.CLEAN, "a disclosure the walk could not reach must not read as symmetric"
    assert result.status == disclosure.VIOLATION
    assert [v["token"] for v in result.violations] == ["warnings_out"]
    assert result.violations[0]["blind"] == ["text"]


def test_return_fixpoint_no_longer_stops_mid_propagation(tmp_path: pathlib.Path) -> None:
    """The ``for _ in range(3)`` instance.

    The fixpoint now runs to convergence under a bound that is a proof
    (``len(funcs) + 1``) rather than a guess (3), so a bucket carried back
    through eight returns reaches the caller that emits it.
    """
    target = tmp_path / "cmd_chain.py"
    target.write_text(deep_return_chain_command(links=8), encoding="utf-8")

    result = disclosure.scan_file(target)

    assert result.status != disclosure.CLEAN
    assert result.status == disclosure.VIOLATION
    assert result.violations[0]["observed_by"] == ["json"]
    assert result.violations[0]["blind"] == ["text"]


def test_closure_cap_marks_the_file_partial_not_clean(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``limit=4000`` instance.

    A truncated callee closure narrows the ``supported`` mode set, which can
    drop a command below the two-mode threshold and produce "no asymmetry
    here" for a command that was never compared. The file must say so.
    """
    monkeypatch.setattr(disclosure, "_EXPAND_LIMIT", 3)
    target = tmp_path / "cmd_wide.py"
    target.write_text(wide_closure_command(links=12), encoding="utf-8")

    result = disclosure.scan_file(target)

    assert result.status == disclosure.PARTIAL
    assert result.status != disclosure.CLEAN
    assert result.caps_hit.get(disclosure.CAP_CALLEE_CLOSURE, 0) > 0
    assert result.reason and disclosure.CAP_CALLEE_CLOSURE in result.reason
    assert not result.fully_analysed


def test_a_truncated_file_is_counted_and_exits_non_zero(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract 2 — the cap count is published, and ``ok`` is false."""
    monkeypatch.setattr(disclosure, "_EXPAND_LIMIT", 3)
    (tmp_path / "cmd_wide.py").write_text(wide_closure_command(links=12), encoding="utf-8")
    (tmp_path / "cmd_fine.py").write_text(SYMMETRIC_COMMAND, encoding="utf-8")

    report = disclosure.scan(tmp_path)

    assert report.violations == [], "the exit must come from the truncation alone, not from a finding"
    assert report.files_parsed == 2, "a truncated file was still read and parsed"
    assert report.files_partial == 1
    assert report.files_capped == 1
    assert report.cap_hits.get(disclosure.CAP_CALLEE_CLOSURE, 0) > 0
    assert [entry["module"] for entry in report.capped] == ["cmd_wide.py"]
    assert not report.ok, "0 violations from a partially-analysed tree is not a clean result"
    assert "1 truncated" in report.summary()


def test_cli_exit_code_reflects_a_truncated_analysis(tmp_path: pathlib.Path) -> None:
    """Contract 2, end to end through ``main`` — the number CI reads.

    The truncation is produced by the REAL cap: 4001 chained helpers, i.e. a
    callee closure the shipped ``_EXPAND_LIMIT`` genuinely cannot finish. No
    monkeypatching reaches a subprocess, and a cap that is only ever tripped
    by a patched constant has not been shown to trip.
    """
    (tmp_path / "cmd_huge.py").write_text(wide_closure_command(links=4001), encoding="utf-8")

    result = _run_cli(["--root", str(tmp_path)])
    payload = json.loads(result.stdout)

    assert payload["files_capped"] == 1
    assert payload["cap_hits"][disclosure.CAP_CALLEE_CLOSURE] > 0
    assert payload["violations"] == []
    assert result.returncode == disclosure.EXIT_UNANALYZABLE, (
        f"a truncated analysis must not exit 0 or exit as a plain violation: {result.returncode}"
    )


def test_baseline_regeneration_refuses_a_truncated_tree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ratchet regenerated from a truncated scan records "fixed" for what it missed."""
    monkeypatch.setattr(disclosure, "_EXPAND_LIMIT", 3)
    (tmp_path / "cmd_wide.py").write_text(wide_closure_command(links=12), encoding="utf-8")

    report = disclosure.scan(tmp_path)

    assert report.files_capped == 1
    assert not report.ok


# ---------------------------------------------------------------------------
# 2. The pre-fix proof
# ---------------------------------------------------------------------------


def test_prefix_scanner_reports_these_corpora_clean(tmp_path: pathlib.Path) -> None:
    """Run ``HEAD``'s scanner over the planted corpora: both come back clean.

    This is the falsifier for the two behavioural tests above. If it ever
    fails, the corpora stopped exercising the caps and the tests that assert
    the post-fix behaviour prove nothing.
    """
    prefix = tmp_path / "prefix_scanner.py"
    source = subprocess.run(
        ["git", "show", "HEAD:scripts/scan_disclosure_asymmetry.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if "_MAX_DEPTH" not in source:
        pytest.skip("HEAD already carries the W1459 fix; the pre-fix arm is history")
    prefix.write_text(source, encoding="utf-8")

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "cmd_deep.py").write_text(deep_helper_command(links=20), encoding="utf-8")
    (corpus / "cmd_chain.py").write_text(deep_return_chain_command(links=8), encoding="utf-8")

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib.util, json, pathlib, sys\n"
        f"spec = importlib.util.spec_from_file_location('prefix_scanner', r'{prefix}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['prefix_scanner'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        f"results = mod.scan_files(pathlib.Path(r'{corpus}'))\n"
        "print(json.dumps({r.module: [r.status, len(r.violations)] for r in results}))\n",
        encoding="utf-8",
    )
    out = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, check=True)
    prefix_status = json.loads(out.stdout)

    assert prefix_status["cmd_deep.py"] == ["clean", 0], (
        "the deep-helper corpus must be reported CLEAN by pre-fix code, or it is not exercising the depth cap"
    )
    assert prefix_status["cmd_chain.py"] == ["clean", 0], (
        "the deep-return corpus must be reported CLEAN by pre-fix code, or it is not exercising the fixpoint cap"
    )


# ---------------------------------------------------------------------------
# 3. roam secrets — the denominator must not claim a file it never read
# ---------------------------------------------------------------------------


def test_scan_file_reports_a_read_failure_instead_of_an_empty_list(tmp_path: pathlib.Path) -> None:
    from roam.commands.cmd_secrets import scan_file

    errors: list[dict] = []
    findings = scan_file(str(tmp_path / "does_not_exist.py"), read_errors=errors)

    assert findings == []
    assert len(errors) == 1, "an empty finding list from a file that was never opened is the two-valued defect"
    assert "does_not_exist" in errors[0]["file"]
    assert errors[0]["error"]


def test_unreadable_file_is_not_in_the_scanned_denominator(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The assigned instance: ``files_scanned += 1`` sat ABOVE the read.

    Pre-fix this asserted 2 — the unreadable file was inside the number the
    verdict quotes as "files scanned".
    """
    from pathlib import Path as _Path

    from roam.commands.cmd_secrets import scan_project

    (tmp_path / "readable.py").write_text("x = 1\n", encoding="utf-8")
    blocked = tmp_path / "blocked.py"
    blocked.write_text("y = 2\n", encoding="utf-8")

    real_read_bytes = _Path.read_bytes

    def refuse(self):
        if self.name == "blocked.py":
            raise OSError(13, "Permission denied")
        return real_read_bytes(self)

    monkeypatch.setattr(_Path, "read_bytes", refuse)

    stats: dict = {}
    findings = scan_project(tmp_path, use_index=False, include_tests=True, stats=stats)

    assert findings == []
    assert stats["files_scanned"] == 1, "a file whose bytes never arrived cannot be counted as scanned"
    assert stats["files_unreadable"] == 1
    assert [e["file"] for e in stats["read_errors"]][0].endswith("blocked.py")


def test_secrets_denominator_partitions_the_candidate_list(tmp_path: pathlib.Path) -> None:
    """Every listed file lands in exactly one published bucket.

    Without this, "files_listed - files_scanned" is an unexplained gap, and
    an unexplained gap is where a silently dropped file hides.
    """
    from roam.commands.cmd_secrets import scan_project

    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("hello\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n")

    stats: dict = {}
    scan_project(tmp_path, use_index=False, include_tests=False, stats=stats)

    assert (
        stats["files_listed"]
        == stats["files_scanned"] + stats["files_unreadable"] + stats["files_unresolved"] + stats["files_filtered"]
    )
    assert stats["files_undiscoverable"] == 0


def test_secrets_json_and_text_both_name_an_unread_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The disclosure must reach the human branch too, not only the envelope."""
    from pathlib import Path as _Path

    from roam.commands.cmd_secrets import scan_project

    (tmp_path / "readable.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "blocked.py").write_text("y = 2\n", encoding="utf-8")

    real_read_bytes = _Path.read_bytes

    def refuse(self):
        if self.name == "blocked.py":
            raise OSError(13, "Permission denied")
        return real_read_bytes(self)

    monkeypatch.setattr(_Path, "read_bytes", refuse)
    stats: dict = {}
    scan_project(tmp_path, use_index=False, include_tests=True, stats=stats)

    # The command derives ``scan_incomplete`` from exactly these numbers and
    # threads it into the shared verdict, which all three output modes print.
    assert stats["files_unreadable"] > 0
    assert stats["files_unreadable"] + stats["files_undiscoverable"] > 0


# ---------------------------------------------------------------------------
# 4. NEGATIVE CONTROLS — "mark everything partial" must not pass
# ---------------------------------------------------------------------------


def test_negative_control_a_normal_file_is_still_clean(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "cmd_ok.py"
    target.write_text(SYMMETRIC_COMMAND, encoding="utf-8")

    result = disclosure.scan_file(target)

    assert result.status == disclosure.CLEAN
    assert result.caps_hit == {}
    assert result.fully_analysed


def test_negative_control_clean_tree_exits_zero(tmp_path: pathlib.Path) -> None:
    for name in ("cmd_alpha.py", "cmd_beta.py"):
        (tmp_path / name).write_text(SYMMETRIC_COMMAND, encoding="utf-8")

    report = disclosure.scan(tmp_path)
    result = _run_cli(["--root", str(tmp_path)])

    assert report.files_capped == 0
    assert report.files_partial == 0
    assert report.cap_hits == {}
    assert report.ok
    assert "0 truncated" in report.summary()
    assert result.returncode == disclosure.EXIT_OK, result.stderr


def test_negative_control_real_command_tree_is_not_truncated() -> None:
    """The shipped tree is analysed IN FULL — the caps are not firing on it.

    This is the assertion that stops the fix from being "call everything
    partial". It is also the measurement that justified deleting the depth
    cap rather than raising it: the walk reached depth 10 of 12 at HEAD, so
    the bound had two levels of headroom and was one refactor from silently
    truncating a shipped module.
    """
    report = disclosure.scan(REPO_ROOT / "src" / "roam" / "commands")

    assert report.files_parsed > 200, report.summary()
    assert report.files_capped == 0, f"the shipped tree was only partially analysed: {report.capped}"
    assert report.cap_hits == {}


def test_negative_control_secrets_counts_every_readable_file(tmp_path: pathlib.Path) -> None:
    from roam.commands.cmd_secrets import scan_project

    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")

    stats: dict = {}
    scan_project(tmp_path, use_index=False, include_tests=True, stats=stats)

    assert stats["files_scanned"] == 3, "a readable project must not be reported as partly unread"
    assert stats["files_unreadable"] == 0
    assert stats["read_errors"] == []
