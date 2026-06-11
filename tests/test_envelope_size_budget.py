"""Envelope byte-budget ratchet — probe bloat fails CI, not production.

The routing locks pin WHERE prompts go; nothing pinned how BIG the
envelopes get. A probe that starts embedding whole files (the historical
full_file_body class of regressions) silently multiplies the per-prompt
injection cost across every agent turn. These ceilings are coarse —
roughly 4× the sizes measured at introduction — so they trip on
order-of-magnitude bloat, not on repo growth.

Skips cleanly when the index is absent (public CI checkout) — this is a
dogfood-environment lock, like the corpus replay.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from click.testing import CliRunner
from conftest import invoke_cli  # noqa: E402

from roam.cli import cli  # noqa: F401 — imported for invoke_cli's registry
from tests._helpers.repo_root import repo_root

# (procedure sentinel prompt, byte ceiling). Measured sizes at introduction:
# callers 7.1K, coupling 2.8K, freeform 3.6K, describe 13.1K, trace 3.6K,
# defined-where 3.7K, history 4.7K, repo-structure 2.8K.
_SENTINELS = (
    ("who calls open_db?", 32_000),
    ("which files depend on cli.py", 16_000),
    ("explain how the indexer pipeline works", 16_000),
    ("what does src/roam/atomic_io.py do", 56_000),
    ("trace the login flow", 16_000),
    ("where is open_db defined", 16_000),
    ("what changed in src/roam/cli.py last week", 24_000),
    ("what are the layers of this codebase", 16_000),
)


@pytest.mark.skipif(not (repo_root() / ".roam" / "index.db").exists(), reason="index absent (public CI)")
def test_sentinel_envelopes_stay_within_budget(monkeypatch):
    root = repo_root()
    monkeypatch.chdir(root)
    runner = CliRunner()
    oversized = []
    for prompt, ceiling in _SENTINELS:
        res = invoke_cli(runner, ["--json", "compile", prompt], cwd=root)
        assert res.exit_code == 0, res.output
        size = len(res.output.encode("utf-8"))
        json.loads(res.output)  # still a valid envelope
        if size > ceiling:
            oversized.append((prompt, size, ceiling))
    assert not oversized, (
        f"envelope bloat — a probe is embedding far more than at introduction (size vs ceiling): {oversized}"
    )
