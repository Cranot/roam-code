"""Planted-issues RECALL eval for the verify loop.

The FP side of verify quality is locked by the dogfood suites
(test_verify_dogfood_fps.py — clean code must stay quiet). This suite locks
the RECALL side: every check category must catch its canonical planted
positive. Together they pin the detector quality the README cites.

Each test plants one unambiguous issue of a single category in a small
indexed repo and asserts `roam verify` flags it in that category. The repo
is built once per module (session-scoped fixture) and re-indexed; checks
run via the CLI with --json for structural assertions.

Secrets-category note: credential-shaped strings are CONSTRUCTED at runtime
("AKIA" + ...) so this file never trips the repo's own leak gates.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

# xdist: builds + indexes one shared tmp repo per worker is fine (tmp_path
# isolation); no group needed.


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "roam", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture(scope="module")
def planted_repo(tmp_path_factory):
    """A git repo with a snake_case-voting production base + planted issues."""
    repo = tmp_path_factory.mktemp("verify_recall_repo")
    src = repo / "src"
    src.mkdir()
    # Production base: 12 snake_case functions across 3 files so the naming
    # convention has a clear majority to vote with.
    for i in range(3):
        body = "\n\n".join(f"def compute_value_{i}_{j}(x):\n    return x + {j}" for j in range(4))
        (src / f"base_{i}.py").write_text(body + "\n", encoding="utf-8")

    # ---- planted positives, one file per category ----
    (src / "planted_naming.py").write_text("def BadlyNamedThing(x):\n    return x\n", encoding="utf-8")
    (src / "planted_imports.py").write_text(
        "from totally_nonexistent_module_zq import helper_fn\n\ndef use_it():\n    return helper_fn()\n",
        encoding="utf-8",
    )
    (src / "planted_error_handling.py").write_text(
        "def swallow_everything(x):\n    try:\n        return 1 / x\n    except Exception:\n        pass\n",
        encoding="utf-8",
    )
    (src / "planted_syntax.py").write_text("def broken(:\n    return 1\n", encoding="utf-8")
    nested = "def tangled(a, b, c, d):\n    total = 0\n"
    indent = "    "
    for depth in range(8):
        nested += indent * (depth + 1) + f"if a > {depth}:\n"
        nested += indent * (depth + 2) + f"for i in range(b + {depth}):\n"
        indent_body = indent * (depth + 3)
        nested += indent_body + f"total += i if c > {depth} else d\n"
    nested += "    return total\n"
    (src / "planted_complexity.py").write_text(nested, encoding="utf-8")
    fake_key = "AKIA" + "IOSFODNN7" + "EXAMPLE"
    fake_secret = "aws_secret_access_key = " + repr("wJalrXUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY")
    (src / "planted_secrets.py").write_text(f'AWS_ACCESS_KEY_ID = "{fake_key}"\n{fake_secret}\n', encoding="utf-8")

    (repo / "pyproject.toml").write_text(
        '[project]\nname = "planted"\nversion = "0.0.1"\ndependencies = ["click>=8.0"]\n',
        encoding="utf-8",
    )
    (src / "uses_declared_dep.py").write_text(
        "import click\n\nimport os\n\nfrom src.base_0 import compute_value_0_0\n\n"
        "def go():\n    return click, os, compute_value_0_0\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    r = _run(["init"], repo)
    assert r.returncode == 0, r.stdout + r.stderr
    return repo


CASES = [
    ("naming", "src/planted_naming.py", "BadlyNamedThing"),
    ("imports", "src/planted_imports.py", "totally_nonexistent_module_zq"),
    ("error_handling", "src/planted_error_handling.py", "swallow"),
    ("syntax", "src/planted_syntax.py", "invalid syntax"),
    ("complexity", "src/planted_complexity.py", "tangled"),
]


@pytest.mark.parametrize("category,path,needle", CASES, ids=[c[0] for c in CASES])
def test_planted_issue_is_caught(planted_repo, category, path, needle):
    r = _run(["--json", "verify", path, "--checks", category, "--threshold", "100"], planted_repo)
    env = json.loads(r.stdout)
    violations = env.get("violations") or env.get("findings") or []
    cat_hits = [v for v in violations if category in str(v.get("category", v.get("check", "")))]
    assert cat_hits, (
        f"{category}: planted issue in {path} not caught. "
        f"verdict={env.get('summary', {}).get('verdict')} violations={violations[:3]}"
    )
    assert any(needle in json.dumps(v) for v in cat_hits), (
        f"{category}: caught something, but not the planted `{needle}`: {cat_hits[:3]}"
    )


def test_planted_secret_is_caught(planted_repo):
    """Secrets ride the leak gate — run verify over the planted file and
    require a secrets-category failure naming the file."""
    r = _run(["--json", "verify", "src/planted_secrets.py", "--checks", "secrets"], planted_repo)
    env = json.loads(r.stdout)
    blob = json.dumps(env)
    assert "planted_secrets.py" in blob and ("secret" in blob.lower()), (
        f"secrets: planted credential not flagged. verdict={env.get('summary', {}).get('verdict')}"
    )


def test_clean_base_files_stay_quiet(planted_repo):
    """Precision guard riding the same corpus: the snake_case production
    base must produce ZERO findings across the same checks."""
    r = _run(
        [
            "--json",
            "verify",
            "src/base_0.py",
            "src/base_1.py",
            "src/base_2.py",
            "--checks",
            "naming,imports,error_handling,syntax,complexity",
        ],
        planted_repo,
    )
    env = json.loads(r.stdout)
    violations = env.get("violations") or env.get("findings") or []
    assert not violations, f"clean base flagged: {violations[:5]}"


def test_declared_dep_and_internal_imports_stay_quiet(planted_repo):
    """FP guards for the in-loop firewall: a declared dependency (click),
    stdlib (os), and an internal module import must produce ZERO imports
    violations."""
    r = _run(
        ["--json", "verify", "src/uses_declared_dep.py", "--checks", "imports", "--threshold", "100"],
        planted_repo,
    )
    env = json.loads(r.stdout)
    violations = [v for v in (env.get("violations") or []) if v.get("category") == "imports"]
    assert not violations, f"clean imports flagged: {violations[:3]}"
