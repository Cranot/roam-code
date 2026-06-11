"""Self-dogfood lock: verify's noisy-rule regressions fail CI on roam itself.

History: the naming rule once reported ~2000 false positives on an external
repo (test names poisoned the convention sample), and the secrets gate's
first cut flagged 40 of roam's own fixture files. Both classes are fixed —
this lock pins them at ZERO on a fixed set of roam's own production files,
so a rule change that re-floods false positives fails here in seconds
instead of surfacing as a screaming whole-repo report days later.

Scoped + fast (a handful of files, two checks) — NOT the multi-minute
whole-repo report.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import json

from click.testing import CliRunner
from conftest import invoke_cli  # noqa: E402

from tests._helpers.repo_root import repo_root

# Production files with stable, convention-clean naming and zero secrets.
# A naming or secrets finding on ANY of these is a rule regression, not debt.
_LOCK_FILES = (
    "src/roam/atomic_io.py",
    "src/roam/observability.py",
    "src/roam/db/connection.py",
    "src/roam/commands/resolve.py",
    "src/roam/output/formatter.py",
)


def test_naming_and_secrets_stay_clean_on_own_source(monkeypatch):
    root = repo_root()
    monkeypatch.chdir(root)
    runner = CliRunner()
    res = invoke_cli(
        runner,
        ["--json", "verify", *_LOCK_FILES, "--checks", "naming,secrets"],
        cwd=root,
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    cats = d.get("categories") or {}
    noisy = []
    for cat in ("naming", "secrets"):
        for v in (cats.get(cat) or {}).get("violations") or []:
            noisy.append((cat, v.get("file"), v.get("line"), (v.get("message") or "")[:80]))
    assert not noisy, f"rule regression: false positives re-appeared on roam's own clean production files: {noisy}"
