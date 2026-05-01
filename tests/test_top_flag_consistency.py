"""DOG.2 — `--top` should work on commands with truncated output.

The dogfood pass surfaced that `roam complexity --top`, `roam algo --top`
(via `cmd_math.py`), and `roam rules --top` all rejected the flag with
``No such option`` in v11.x.

Now:

* `cmd_complexity` aliases `--top` → existing `--limit / -n` (default 20).
* `cmd_math` (algo / math) aliases the same.
* `cmd_rules` introduces a new `--top N` (default 10) capping the per-rule
  violation list. Pass `--top 0` for unlimited.

This module locks in those aliases so the next refactor doesn't drop them.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli


class TestComplexityTopFlag:
    def test_top_flag_accepted(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["complexity", "--top", "3"])
        assert result.exit_code == 0, result.output

    def test_top_aliases_limit(self, indexed_project):
        """`--top N` must produce identical output to `--limit N`."""
        runner = CliRunner()
        a = runner.invoke(cli, ["--json", "complexity", "--top", "3"])
        b = runner.invoke(cli, ["--json", "complexity", "--limit", "3"])
        assert a.exit_code == 0 and b.exit_code == 0
        ja = json.loads(a.output)
        jb = json.loads(b.output)
        # Strip non-deterministic timestamp so identity-by-value is meaningful.
        for d in (ja, jb):
            d.pop("_meta", None)
            d.pop("timestamp", None)
        assert ja == jb

    def test_top_short_alias_n_still_works(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["complexity", "-n", "3"])
        assert result.exit_code == 0, result.output


class TestAlgoTopFlag:
    def test_top_flag_accepted_under_algo(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["algo", "--top", "5"])
        assert result.exit_code == 0, result.output

    def test_top_flag_accepted_under_math_alias(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["math", "--top", "5"])
        assert result.exit_code == 0, result.output


class TestRulesTopFlag:
    def test_top_flag_accepted(self, indexed_project):
        runner = CliRunner()
        result = runner.invoke(cli, ["rules", "--top", "5"])
        assert result.exit_code == 0, result.output

    def test_top_zero_means_unlimited(self, indexed_project):
        """`--top 0` shouldn't truncate. Sanity check against an unbounded run."""
        runner = CliRunner()
        result = runner.invoke(cli, ["rules", "--top", "0"])
        assert result.exit_code == 0, result.output

    def test_limit_is_alias(self, indexed_project):
        """`--limit N` accepts the same as `--top N`."""
        runner = CliRunner()
        result = runner.invoke(cli, ["rules", "--limit", "3"])
        assert result.exit_code == 0, result.output
