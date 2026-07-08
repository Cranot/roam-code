"""Deterministic replay-gate: the prove-before-trust spine for SPN v1.

Given (a consumer repo, a candidate_patch, the consumer's OWN validation
command), this certifies a defect transfer entirely inside a throwaway git
worktree:

    1. PRE-PATCH  — run the validation command; the defect must FIRE
                    (non-zero exit) or the sibling is not a real target.
    2. APPLY      — ``git apply`` the candidate_patch in the worktree only.
    3. POST-PATCH — run the validation command again; it must CLEAR
                    (zero exit) for a green fusion_attestation.

Security / trust model:
  * The executed command is the CONSUMER's own (passed by the caller). The
    untrusted claim's ``replay_predicate`` is a *label*, never executed.
  * Everything happens in a detached throwaway worktree off HEAD. The real
    working tree is never modified; nothing is ever committed or pushed.
    This is a propose-only certifier.
  * ``git apply`` transforms text; it does not execute code. The only code that
    runs is the consumer's own validation command (dual-use residual — see the
    command's propose-only + human-in-the-loop framing).

Deterministic (Rule 10): the gate has no learned state; identical inputs and a
identical repo HEAD produce the identical attestation (modulo the validation
command's own determinism).
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_S = 600
_OUTPUT_TAIL = 4000


@dataclasses.dataclass(frozen=True)
class FusionAttestation:
    """Proof-carrying result of a replay-gate run.

    ``green`` requires: the defect fired pre-patch, the patch applied, and the
    predicate cleared post-patch. Anything else is honestly labelled.
    """

    status: str  # green | red | not_applicable | patch_failed | skipped | error
    pre_patch_fired: bool
    post_patch_cleared: bool
    patch_applied: bool
    pre_exit: int | None
    post_exit: int | None
    validation_command: str
    base_ref: str
    localized: bool
    detail: str
    retargeted_to: str | None = None

    def is_green(self) -> bool:
        return self.status == "green"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pre_patch_fired": self.pre_patch_fired,
            "post_patch_cleared": self.post_patch_cleared,
            "patch_applied": self.patch_applied,
            "pre_exit": self.pre_exit,
            "post_exit": self.post_exit,
            "validation_command": self.validation_command,
            "base_ref": self.base_ref,
            "localized": self.localized,
            "retargeted_to": self.retargeted_to,
            "detail": self.detail,
        }


def _skipped(command: str, detail: str) -> FusionAttestation:
    return FusionAttestation(
        status="skipped",
        pre_patch_fired=False,
        post_patch_cleared=False,
        patch_applied=False,
        pre_exit=None,
        post_exit=None,
        validation_command=command or "",
        base_ref="",
        localized=False,
        detail=detail,
    )


def _error(command: str, base_ref: str, detail: str) -> FusionAttestation:
    return FusionAttestation(
        status="error",
        pre_patch_fired=False,
        post_patch_cleared=False,
        patch_applied=False,
        pre_exit=None,
        post_exit=None,
        validation_command=command or "",
        base_ref=base_ref,
        localized=False,
        detail=detail,
    )


def _git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _resolve_head(repo: Path) -> str:
    return _git(repo, ["rev-parse", "HEAD"]).stdout.strip()


def _tail(text: str) -> str:
    text = text or ""
    return text[-_OUTPUT_TAIL:]


def retarget_patch(patch_text: str, new_path: str) -> str | None:
    """Rewrite a single-file unified diff's paths onto ``new_path``.

    Returns the retargeted diff, or ``None`` when the diff touches more than one
    file (v1 only retargets single-file patches) or has no recognizable header.
    Deterministic string transform — no code execution.
    """
    new_path = new_path.replace("\\", "/").lstrip("/")
    lines = patch_text.splitlines(keepends=True)
    minus_count = sum(1 for line in lines if line.startswith("--- "))
    plus_count = sum(1 for line in lines if line.startswith("+++ "))
    if minus_count != 1 or plus_count != 1:
        return None
    out: list[str] = []
    saw_header = False
    for line in lines:
        newline = "\n" if line.endswith("\n") else ""
        if line.startswith("diff --git "):
            out.append(f"diff --git a/{new_path} b/{new_path}{newline}")
            saw_header = True
        elif line.startswith("--- "):
            out.append(f"--- a/{new_path}{newline}")
            saw_header = True
        elif line.startswith("+++ "):
            out.append(f"+++ b/{new_path}{newline}")
            saw_header = True
        else:
            out.append(line)
    if not saw_header:
        return None
    return "".join(out)


def _apply_patch(worktree: Path, patch_text: str) -> tuple[bool, str]:
    """Apply a unified diff inside the worktree via ``git apply`` (text-only)."""
    if not patch_text.strip():
        return False, "empty candidate_patch"
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=str(worktree),
        input=patch_text if patch_text.endswith("\n") else patch_text + "\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        return False, _tail(proc.stderr.strip()) or "git apply failed"
    return True, "applied"


def _run_validation(cwd: Path, command: str, timeout: int, env: dict[str, str] | None) -> tuple[int, str]:
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
        timeout=timeout,
        env=run_env,
    )
    return proc.returncode, _tail(proc.stdout)


def run_replay_gate(
    consumer_repo: str | Path,
    candidate_patch: str,
    validation_command: str | None,
    *,
    retarget_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    env: dict[str, str] | None = None,
) -> FusionAttestation:
    """Certify (or refute) a defect transfer in a throwaway worktree.

    Propose-only: never mutates the real tree, never commits, never pushes.
    """
    if not validation_command or not str(validation_command).strip():
        return _skipped(
            validation_command or "",
            "replay skipped: provide the consumer's own --validation-command to certify",
        )

    repo = Path(consumer_repo).resolve()
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        # A worktree checkout has a .git file, not a dir; both are fine. If
        # neither exists this is not a git repo.
        try:
            _git(repo, ["rev-parse", "--git-dir"])
        except Exception:
            return _error(validation_command, "", f"not a git repository: {repo}")

    try:
        base_ref = _resolve_head(repo)
    except Exception as exc:  # noqa: BLE001 - surfaced as an honest error attestation
        return _error(validation_command, "", f"cannot resolve HEAD: {exc}")

    patch_to_apply = candidate_patch
    retargeted_to: str | None = None
    if retarget_file:
        retargeted = retarget_patch(candidate_patch, retarget_file)
        if retargeted is not None:
            patch_to_apply = retargeted
            retargeted_to = retarget_file.replace("\\", "/").lstrip("/")

    tmp_root = tempfile.mkdtemp(prefix="roam-spn-replay-")
    worktree = Path(tmp_root) / "wt"
    added = False
    try:
        _git(repo, ["worktree", "add", "--detach", "--quiet", str(worktree), base_ref])
        added = True

        pre_exit, pre_out = _run_validation(worktree, validation_command, timeout, env)
        pre_patch_fired = pre_exit != 0
        if not pre_patch_fired:
            return FusionAttestation(
                status="not_applicable",
                pre_patch_fired=False,
                post_patch_cleared=False,
                patch_applied=False,
                pre_exit=pre_exit,
                post_exit=None,
                validation_command=validation_command,
                base_ref=base_ref,
                localized=False,
                detail="predicate did not fire pre-patch: the defect is not present at this site",
                retargeted_to=retargeted_to,
            )

        applied, apply_detail = _apply_patch(worktree, patch_to_apply)
        if not applied:
            return FusionAttestation(
                status="patch_failed",
                pre_patch_fired=True,
                post_patch_cleared=False,
                patch_applied=False,
                pre_exit=pre_exit,
                post_exit=None,
                validation_command=validation_command,
                base_ref=base_ref,
                localized=True,
                detail=f"defect fired but candidate_patch did not apply: {apply_detail}",
                retargeted_to=retargeted_to,
            )

        post_exit, post_out = _run_validation(worktree, validation_command, timeout, env)
        post_patch_cleared = post_exit == 0
        status = "green" if post_patch_cleared else "red"
        detail = (
            "defect fired pre-patch and cleared post-patch"
            if post_patch_cleared
            else f"defect fired pre-patch but did NOT clear post-patch (exit={post_exit})"
        )
        return FusionAttestation(
            status=status,
            pre_patch_fired=True,
            post_patch_cleared=post_patch_cleared,
            patch_applied=True,
            pre_exit=pre_exit,
            post_exit=post_exit,
            validation_command=validation_command,
            base_ref=base_ref,
            localized=True,
            detail=detail,
            retargeted_to=retargeted_to,
        )
    except subprocess.TimeoutExpired:
        return _error(validation_command, base_ref, f"validation command timed out after {timeout}s")
    except Exception as exc:  # noqa: BLE001 - any orchestration failure is an honest error attestation
        return _error(validation_command, base_ref, f"replay-gate error: {exc}")
    finally:
        if added:
            _git(repo, ["worktree", "remove", "--force", str(worktree)], check=False)
        shutil.rmtree(tmp_root, ignore_errors=True)
