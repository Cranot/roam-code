"""Regression tests for the higher-order-dispatch blind spot in ``roam dead``.

Background
----------
Measured on a real repo (stoa): ``roam dead --reachable-only`` reported all
17 ``probe_*`` functions in ``autopilot/monitor.py`` as dead, even though
they were the core of a systemd-invoked monitoring dashboard. The functions
were never *called* directly in the source — they were handed to a
dispatcher BY NAME:

    def safe(name, fn):
        ...
        fn(*a)

    def dispatch():
        safe("disk", probe_disk)
        handlers = {"disk": probe_disk, "cpu": probe_cpu}

The Python tree-sitter extractor (``roam.languages.python_lang``) only
emitted a reference for a bare identifier when it was the target of a
``call`` node. A bare identifier used as a call ARGUMENT, or as a
dict/list/tuple/set literal ELEMENT, produced no edge at all — the walker
recursed into containers and argument lists looking only for nested
``call`` nodes, and a plain ``identifier`` leaf in those positions was
silently dropped.

The fix adds three narrowly-scoped extraction rules to
``PythonExtractor._walk_refs`` (mirroring the JS extractor's existing
``_emit_argument_identifier_ref`` "Bug 2" fix for the call-argument case):
bare identifiers in call-argument position (positional and keyword),
and bare identifiers as list/tuple/set elements or dict values. Each
resolves through the normal reference-resolution pipeline into a real
``kind='reference'`` edge in the ``edges`` table — this is a graph fix,
not a text-grep side channel.

Class base lists (``class Foo(Base):``) are deliberately excluded (they
already get an ``inherits`` edge) so the fix doesn't double-write edges
for something already reachable.

These tests cover the three outcomes that prove the fix is precise rather
than a blanket "mark everything reachable":

1. ``test_call_argument_dispatch_marks_target_reachable`` — the exact
   ``safe(name, fn)`` / ``safe("disk", probe_disk)`` shape from the
   defect report (plus the keyword-argument variant).
2. ``test_dict_and_list_registry_marks_targets_reachable`` — the
   ``handlers = {"disk": probe_disk}`` / ``PROBE_LIST = [probe_disk, ...]``
   registry shapes.
3. ``test_genuinely_dead_function_still_reported`` — the negative case: a
   function with NO reference anywhere in the corpus must still show up
   in ``--reachable-only`` output. If this test fails while 1/2 pass, the
   fix over-widened reachability instead of adding the missing edge.
"""

from __future__ import annotations

import json

from tests.conftest import invoke_cli

# A single small corpus exercising every shape in the defect report: a
# `safe(name, fn)` dispatcher called with both a positional and a keyword
# bare-identifier argument, a dict-literal registry, a list-literal
# registry, and one function with no reference anywhere (the negative
# control). `src/main.py` is the only file nothing imports, so it (and
# only it) is the oracle's entry point -- `monitor.py`'s own symbols are
# NOT trivially "reachable by virtue of living in an unimported file",
# which would make this fixture too easy to pass without the fix.
_MONITOR_PROJECT = {
    "src/monitor.py": (
        "def probe_disk():\n"
        '    return "disk ok"\n'
        "\n"
        "\n"
        "def probe_cpu():\n"
        '    return "cpu ok"\n'
        "\n"
        "\n"
        "def probe_mem():\n"
        '    return "mem ok"\n'
        "\n"
        "\n"
        "def probe_net():\n"
        '    return "net ok"\n'
        "\n"
        "\n"
        "def probe_list_a():\n"
        '    return "list a ok"\n'
        "\n"
        "\n"
        "def probe_list_b():\n"
        '    return "list b ok"\n'
        "\n"
        "\n"
        "def genuinely_dead_probe():\n"
        '    return "no caller anywhere in this corpus"\n'
        "\n"
        "\n"
        "def safe(name, fn):\n"
        "    try:\n"
        "        fn()\n"
        "    except Exception:\n"
        "        pass\n"
        "\n"
        "\n"
        'HANDLERS = {"mem": probe_mem, "net": probe_net}\n'
        "PROBE_LIST = [probe_list_a, probe_list_b]\n"
        "\n"
        "\n"
        "def run_all():\n"
        '    safe("disk", probe_disk)\n'
        '    safe(name="cpu", fn=probe_cpu)\n'
        "    for fn in HANDLERS.values():\n"
        "        fn()\n"
        "    for fn in PROBE_LIST:\n"
        "        fn()\n"
    ),
    "src/main.py": ("from monitor import run_all\n\n\ndef main():\n    run_all()\n"),
}


def _reachable_dead_names(cli_runner, project) -> set[str]:
    """Run ``roam --detail --json dead --reachable-only --all`` and return
    the set of exported symbol names still flagged as reachable-only-dead.
    """
    result = invoke_cli(
        cli_runner,
        ["--detail", "dead", "--reachable-only", "--all", "--no-decay"],
        cwd=project,
        json_mode=True,
    )
    assert result.exit_code == 0, result.output
    raw = getattr(result, "stdout", None) or result.output
    brace = raw.find("{")
    assert brace != -1, f"no JSON in dead output:\n{raw[:500]}"
    data = json.loads(raw[brace:])
    names = set()
    for bucket in ("high_confidence", "low_confidence"):
        for finding in data.get(bucket, []):
            value = finding.get("value", finding)
            names.add(value["name"])
    return names


def test_call_argument_dispatch_marks_target_reachable(project_factory, cli_runner):
    """``safe("disk", probe_disk)`` (positional) and ``safe(name="cpu",
    fn=probe_cpu)`` (keyword) are the exact ``def safe(name, fn): fn(*a)``
    dispatch shape from the defect report. Both ``probe_disk`` and
    ``probe_cpu`` are handed to ``safe`` BY NAME and never called
    directly -- they must not appear in the reachable-only dead set.
    """
    project = project_factory(_MONITOR_PROJECT)
    dead_names = _reachable_dead_names(cli_runner, project)

    assert "probe_disk" not in dead_names, (
        "probe_disk is referenced as a bare positional call-argument "
        f'(safe("disk", probe_disk)) but was still reported dead: {dead_names}'
    )
    assert "probe_cpu" not in dead_names, (
        "probe_cpu is referenced as a bare keyword call-argument "
        f'(safe(name="cpu", fn=probe_cpu)) but was still reported dead: {dead_names}'
    )


def test_dict_and_list_registry_marks_targets_reachable(project_factory, cli_runner):
    """``HANDLERS = {"mem": probe_mem, "net": probe_net}`` (dict-of-callables)
    and ``PROBE_LIST = [probe_list_a, probe_list_b]`` (list-of-callables)
    are the registry shapes from the defect report. None of the four
    functions are called directly in source -- they're registered by name
    and invoked through the dict/list at runtime.
    """
    project = project_factory(_MONITOR_PROJECT)
    dead_names = _reachable_dead_names(cli_runner, project)

    for name in ("probe_mem", "probe_net"):
        assert name not in dead_names, (
            f"{name} is registered as a dict-literal value (HANDLERS) but was still reported dead: {dead_names}"
        )
    for name in ("probe_list_a", "probe_list_b"):
        assert name not in dead_names, (
            f"{name} is registered as a list-literal element (PROBE_LIST) but was still reported dead: {dead_names}"
        )


def test_genuinely_dead_function_still_reported(project_factory, cli_runner):
    """Negative case: ``genuinely_dead_probe`` has no reference anywhere in
    the corpus -- not a call, not a container element, not a call
    argument. It MUST still show up in ``--reachable-only`` output.

    This is the test that proves the fix adds the missing EDGE rather than
    disabling the detector: if bare identifiers were unconditionally
    treated as reachability roots (or the oracle BFS were short-circuited),
    this function would vanish from the dead list too.
    """
    project = project_factory(_MONITOR_PROJECT)
    dead_names = _reachable_dead_names(cli_runner, project)

    assert "genuinely_dead_probe" in dead_names, (
        "genuinely_dead_probe has no reference anywhere in the corpus and must "
        f"still be reported dead -- got: {dead_names}. If this is missing while "
        "the probe_* functions are correctly excluded, the fix over-widened "
        "reachability instead of adding the specific missing edge."
    )
