"""Regenerate the _KNOWN_HOOK_BODY_SHAS seed block in cmd_hooks.py.

Enumerates every hook body roam ever shipped (per the git history of
src/roam/commands/cmd_hooks.py), computes the SHA-256 of each body in its
DEPLOYED form — the raw literal for pre-stamp commits, the version-stamped
form after — plus deterministic legacy variants produced by Compile Code
versions that rewrote maintenance invocations before Roam v11 owned the mode
override natively. Compile Code 0.2+ does not perform this transform. Run after
ANY hook-body change and paste the output into the frozenset; the paired
registry test fails when this drifts.

Usage: python scripts/seed_hook_body_shas.py
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILE = "src/roam/commands/cmd_hooks.py"
NAMES = {"_CLAUDE_UPS_HOOK_SCRIPT": "ups", "_CLAUDE_STOP_HOOK_SCRIPT": "stop"}


def surgered(script: str) -> str:
    """Reproduce the historical Compile Code hook transform for old digests.

    This is DATA-GENERATION logic only: it preserves recognition of bodies
    deployed by Compile Code before 0.2. Roam v11 owns the override in its
    canonical body, and current Compile Code does not rewrite hooks or Roam
    source. Roam must never import Compile Code here.
    """
    dynamic_command = '["roam", "--json", *args]'
    overridden_dynamic_command = (
        '["roam", *(["--override-mode"] if args and args[0] in {"verify", "index"} else []), "--json", *args]'
    )
    script = script.replace(dynamic_command, overridden_dynamic_command)
    return re.sub(
        r'(["\']roam["\']\s*,\s*)(["\'])(verify|index)\2',
        r'\1"--override-mode", \2\3\2',
        script,
    )


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def bodies_at(commit: str) -> dict[str, str] | None:
    """Deployed-form hook bodies at a commit, or None if unparseable."""
    try:
        src = git("show", f"{commit}:{FILE}")
    except subprocess.CalledProcessError:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    out: dict[str, str] = {}
    version = None
    marker = "# roam-hook-version:"
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in NAMES and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                out[NAMES[name]] = node.value.value
            if name == "_HOOK_BODY_VERSION" and isinstance(node.value, ast.Constant):
                version = node.value.value
            if name == "_HOOK_VERSION_MARKER" and isinstance(node.value, ast.Constant):
                marker = node.value.value
    if not out:
        return None
    if version is not None:  # post-stamp commit: deployed form carries the marker
        for k, body in out.items():
            lines = body.split("\n", 1)
            rest = lines[1] if len(lines) > 1 else ""
            out[k] = f"{lines[0]}\n{marker} {version}\n{rest}"
        out["_version"] = str(version)
    return out


def main() -> int:
    commits = git("log", "--format=%H %cs", "--follow", "--", FILE).splitlines()
    seen: dict[str, str] = {}  # sha -> provenance
    for line in commits:
        commit, date = line.split()
        found = bodies_at(commit)
        if not found:
            continue
        ver = found.pop("_version", "pre-stamp")
        for kind, body in found.items():
            for variant, text in (("pristine", body), ("surgered", surgered(body))):
                if variant == "surgered" and text == body:
                    continue  # surgery is a no-op on this body
                sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if sha not in seen:
                    label = f"v{ver}" if ver != "pre-stamp" else "pre-stamp"
                    seen[sha] = f"{kind} {label} {variant} ({date} {commit[:8]})"
    print(f"# {len(seen)} distinct deployed hook bodies across history")
    for sha, prov in sorted(seen.items(), key=lambda kv: kv[1]):
        print(f'        "{sha}",  # {prov}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
