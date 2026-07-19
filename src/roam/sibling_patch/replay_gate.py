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
  * Validation runs as structured argv with no implicit shell, an allowlisted
    inherited environment, scrubbed explicit overrides, isolated HOME/TMP
    directories, closed stdin/handles, and a new process group. Windows
    validation is additionally held in a kill-on-close Job Object. A timeout
    terminates the process tree before worktree cleanup.
  * PRE and POST run in separately cloned, detached worktrees materialized
    from the same immutable HEAD object before either validation starts. The
    clones do not share objects, Git config, HOME, caches, or runtime files.
    The real working tree is never modified; nothing is committed or pushed.
    This is a propose-only certifier.
  * ``git apply`` transforms text; it does not execute code. The only code that
    runs is the consumer's own validation command (dual-use residual — see the
    command's propose-only + human-in-the-loop framing).

Containment limit: this is deliberately not advertised as a kernel sandbox.
Validation still runs under the caller's OS account and may access host paths
or the network that account can access. Process groups (POSIX) and a Job Object
(Windows) contain ordinary child trees, but an external container, VM, or OS
sandbox remains required for hostile repository code.

Deterministic (Rule 10): the gate has no learned state; identical inputs and a
identical repo HEAD produce the identical attestation (modulo the validation
command's own determinism).
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from roam.security.redact import redact_secrets_in_string

DEFAULT_TIMEOUT_S = 600
MAX_TIMEOUT_S = 7200
_OUTPUT_TAIL = 4000
_MAX_ARGC = 256
_MAX_ARG_CHARS = 32_768
_GIT_SETUP_TIMEOUT_S = 300
_TRUSTED_GIT_ENV = "ROAM_GIT_BIN"
_WINDOWS_GATE_WRAPPER = (
    "import subprocess,sys\n"
    "if sys.stdin.buffer.read(1) != b'1': raise SystemExit(125)\n"
    "child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, close_fds=True)\n"
    "try:\n"
    "    raise SystemExit(child.wait())\n"
    "except BaseException:\n"
    "    child.kill()\n"
    "    child.wait()\n"
    "    raise\n"
)
_SHELL_CONTROL_TOKENS = frozenset({"|", "||", "&&", ";", "&", ">", ">>", "<", "<<", "2>", "2>>", "2>&1"})
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SENSITIVE_ENV_NAME_RE = re.compile(
    r"(?i)(?:^|_)(?:api_?key|auth|bearer|cookie|credential|database_url|dsn|"
    r"gpg|jwt|key|oauth|pass|passwd|password|private|secret|session|ssh|token)(?:_|$)"
)
_CREDENTIAL_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@")
_INHERITED_ENV_ALLOWLIST = frozenset(
    {
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
    }
)
_CONTROLLED_ENV_KEYS = frozenset(
    {
        "HOME",
        "PATH",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
        "TMP",
        "TEMP",
        "COMSPEC",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GIT_CONFIG_COUNT",
        "PYTHONNOUSERSITE",
        "PYTHONPYCACHEPREFIX",
        "PIP_CONFIG_FILE",
        "PIP_CACHE_DIR",
        "NPM_CONFIG_USERCONFIG",
        "NPM_CONFIG_CACHE",
        "YARN_CACHE_FOLDER",
        "UV_CACHE_DIR",
        "CARGO_HOME",
        "RUSTUP_HOME",
        "GRADLE_USER_HOME",
        "GOCACHE",
        "AWS_EC2_METADATA_DISABLED",
    }
)
_EXECUTION_CONTROL_ENV_NAMES = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "NODE_OPTIONS",
        "NODE_PATH",
        "PERL5LIB",
        "PERL5OPT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "RUBYLIB",
        "RUBYOPT",
        "SHELLOPTS",
        "ZDOTDIR",
    }
)

ValidationCommand = str | Sequence[str]


def _safe_text(value: Any) -> str:
    """Redact known credential shapes before text crosses the gate boundary."""
    redacted, _had_secret = redact_secrets_in_string(str(value or ""))
    return redacted


def _is_sensitive_name(value: str) -> bool:
    normalized = value.strip().lstrip("-").replace("-", "_")
    return bool(_SENSITIVE_ENV_NAME_RE.search(normalized))


def _display_validation_argv(argv: Sequence[str]) -> str:
    """Render argv for attestations while redacting credential-valued flags."""
    safe_args: list[str] = []
    redact_next = False
    for arg in argv:
        value = str(arg)
        if redact_next:
            safe_args.append("[REDACTED]")
            redact_next = False
            continue
        if "=" in value:
            key, _separator, _raw_value = value.partition("=")
            if _is_sensitive_name(key):
                safe_args.append(f"{key}=[REDACTED]")
                continue
        safe_args.append(_safe_text(value))
        if _is_sensitive_name(value):
            redact_next = True
    rendered = subprocess.list2cmdline(safe_args) if os.name == "nt" else shlex.join(safe_args)
    return _safe_text(rendered)


def _display_command_input(command: ValidationCommand | None) -> str:
    if command is None:
        return ""
    if isinstance(command, str):
        try:
            argv = _split_windows_commandline(command) if os.name == "nt" else shlex.split(command, posix=True)
        except (OSError, ValueError):
            return _safe_text(command)
        return _display_validation_argv(argv)
    if isinstance(command, Sequence) and not isinstance(command, (bytes, bytearray)):
        return _display_validation_argv([str(part) for part in command])
    return ""


def _split_windows_commandline(command: str) -> list[str]:
    """Parse a Windows command line with CommandLineToArgvW semantics."""
    import ctypes
    from ctypes import wintypes

    argc = ctypes.c_int()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = (wintypes.LPVOID,)
    parsed = shell32.CommandLineToArgvW(command, ctypes.byref(argc))
    if not parsed:
        raise ValueError("Windows command-line parsing failed")
    try:
        return [parsed[index] for index in range(argc.value)]
    finally:
        kernel32.LocalFree(parsed)


def _normalize_validation_argv(command: ValidationCommand) -> list[str]:
    """Normalize a CLI string or an explicit sequence into safe argv.

    Strings are parsed once for compatibility with ``--validation-command``.
    No shell is started, and shell composition/redirection tokens are rejected
    rather than being silently interpreted as ordinary arguments.
    """
    if isinstance(command, str):
        if not command.strip():
            return []
        argv = _split_windows_commandline(command) if os.name == "nt" else shlex.split(command, posix=True)
    elif isinstance(command, Sequence) and not isinstance(command, (bytes, bytearray)):
        argv = [str(part) for part in command]
    else:
        raise ValueError("validation command must be a string or argv sequence")

    if not argv:
        return []
    if len(argv) > _MAX_ARGC:
        raise ValueError("validation command has too many arguments")
    if any(not arg or "\x00" in arg or len(arg) > _MAX_ARG_CHARS for arg in argv):
        raise ValueError("validation command contains an empty, NUL, or oversized argument")
    controls = sorted({arg for arg in argv if arg in _SHELL_CONTROL_TOKENS})
    if controls:
        raise ValueError(
            f"shell operators are unsupported; pass one executable and its argv (found {', '.join(controls)})"
        )
    return argv


def _ambient_env_value(name: str) -> str | None:
    """Read an ambient variable case-insensitively without copying the env."""
    wanted = name.upper()
    for key, value in os.environ.items():
        if key.upper() == wanted:
            return value
    return None


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        root_text = os.path.normcase(str(root.resolve(strict=False)))
        candidate_text = os.path.normcase(str(candidate.resolve(strict=False)))
        return os.path.commonpath((root_text, candidate_text)) == root_text
    except (OSError, ValueError):
        return False


def _absolute_path_directories(*, excluded_roots: Sequence[Path]) -> list[Path]:
    """Return canonical absolute PATH directories outside protected roots."""
    raw_path = _ambient_env_value("PATH") or ""
    directories: list[Path] = []
    seen: set[str] = set()
    for raw_entry in raw_path.split(os.pathsep):
        entry = raw_entry.strip().strip('"')
        candidate = Path(entry) if entry else None
        if candidate is None or not candidate.is_absolute():
            continue
        try:
            canonical = candidate.resolve(strict=True)
            if not canonical.is_dir():
                continue
        except OSError:
            continue
        if any(_is_within(root, canonical) for root in excluded_roots):
            continue
        key = os.path.normcase(str(canonical))
        if key not in seen:
            seen.add(key)
            directories.append(canonical)
    return directories


def _usable_git_executable(candidate: Path, repo: Path) -> Path | None:
    """Validate one absolute Git candidate and return its canonical path."""
    if not candidate.is_absolute():
        return None
    try:
        canonical = candidate.resolve(strict=True)
        metadata = canonical.stat()
        if not canonical.is_file() or not os.access(canonical, os.X_OK):
            return None
    except OSError:
        return None
    if _is_within(repo, canonical):
        return None

    if os.name == "nt":
        try:
            temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        except OSError:
            temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
        if _is_within(temp_root, canonical):
            return None
    else:
        effective_uid = os.geteuid() if hasattr(os, "geteuid") else metadata.st_uid
        if metadata.st_uid not in {0, effective_uid} or metadata.st_mode & 0o022:
            return None
        parent = canonical.parent
        while True:
            try:
                if parent.stat().st_mode & 0o022:
                    return None
            except OSError:
                return None
            if parent.parent == parent:
                break
            parent = parent.parent
    return canonical


def _resolve_trusted_git(repo: Path) -> Path:
    """Resolve Git once to a canonical executable outside the target repo.

    ``ROAM_GIT_BIN`` is an explicit fail-closed override. Otherwise only
    absolute, existing PATH directories outside the repository are searched;
    relative entries and repository-local shims are never eligible.
    """
    configured = _ambient_env_value(_TRUSTED_GIT_ENV)
    if configured is not None and configured.strip():
        configured_path = Path(configured.strip().strip('"'))
        if not configured_path.is_absolute():
            raise RuntimeError(f"{_TRUSTED_GIT_ENV} must name an absolute executable")
        trusted = _usable_git_executable(configured_path, repo)
        if trusted is None:
            raise RuntimeError(f"{_TRUSTED_GIT_ENV} does not name a trusted Git executable")
        return trusted

    names = ("git.exe", "git.com") if os.name == "nt" else ("git",)
    for directory in _absolute_path_directories(excluded_roots=(repo,)):
        for name in names:
            trusted = _usable_git_executable(directory / name, repo)
            if trusted is not None:
                return trusted
    raise RuntimeError("trusted Git executable unavailable")


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def _runtime_layout(runtime_root: Path) -> dict[str, Path]:
    root = _private_directory(runtime_root)
    names = (
        "home",
        "tmp",
        "config",
        "cache",
        "data",
        "state",
        "runtime",
        "appdata",
        "pycache",
        "pip-cache",
        "npm-cache",
        "yarn-cache",
        "uv-cache",
        "cargo-home",
        "rustup-home",
        "gradle-home",
        "go-cache",
        "hooks",
        "template",
    )
    return {name: _private_directory(root / name) for name in names}


def _base_child_env(*, excluded_roots: Sequence[Path], include_path: bool) -> dict[str, str]:
    child: dict[str, str] = {}
    for key in _INHERITED_ENV_ALLOWLIST:
        value = _ambient_env_value(key)
        if value and not _environment_value_is_sensitive(value):
            child[key] = value
    if include_path:
        directories = _absolute_path_directories(excluded_roots=excluded_roots)
        if directories:
            child["PATH"] = os.pathsep.join(str(path) for path in directories)
    if os.name == "nt":
        system_root = child.get("SYSTEMROOT") or child.get("WINDIR")
        if system_root:
            command_processor = Path(system_root) / "System32" / "cmd.exe"
            if command_processor.is_file():
                child["COMSPEC"] = str(command_processor.resolve())
    return child


def _apply_git_config_overrides(run_env: dict[str, str], overrides: Sequence[tuple[str, str]]) -> None:
    run_env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for index, (key, value) in enumerate(overrides):
        run_env[f"GIT_CONFIG_KEY_{index}"] = key
        run_env[f"GIT_CONFIG_VALUE_{index}"] = value


def _build_git_env(runtime_root: Path, git_executable: Path, protected_repo: Path) -> dict[str, str]:
    """Build a closed Git setup environment with all execution hooks disabled."""
    layout = _runtime_layout(runtime_root)
    empty_config = runtime_root / "empty.gitconfig"
    empty_attributes = runtime_root / "empty.gitattributes"
    empty_config.touch(exist_ok=True)
    empty_attributes.touch(exist_ok=True)
    run_env = _base_child_env(excluded_roots=(protected_repo,), include_path=False)

    path_directories = [git_executable.parent]
    if os.name == "nt":
        system_root = run_env.get("SYSTEMROOT") or run_env.get("WINDIR")
        if system_root:
            for candidate in (Path(system_root) / "System32", Path(system_root)):
                if candidate.is_dir():
                    path_directories.append(candidate.resolve())
    run_env.update(
        {
            "PATH": os.pathsep.join(dict.fromkeys(str(path) for path in path_directories)),
            "HOME": str(layout["home"]),
            "USERPROFILE": str(layout["home"]),
            "APPDATA": str(layout["appdata"]),
            "LOCALAPPDATA": str(layout["appdata"]),
            "XDG_CONFIG_HOME": str(layout["config"]),
            "XDG_CACHE_HOME": str(layout["cache"]),
            "XDG_DATA_HOME": str(layout["data"]),
            "XDG_STATE_HOME": str(layout["state"]),
            "XDG_RUNTIME_DIR": str(layout["runtime"]),
            "TMPDIR": str(layout["tmp"]),
            "TMP": str(layout["tmp"]),
            "TEMP": str(layout["tmp"]),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "GIT_CONFIG_SYSTEM": str(empty_config),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "",
        }
    )
    _apply_git_config_overrides(
        run_env,
        (
            ("core.hooksPath", str(layout["hooks"])),
            ("init.templateDir", str(layout["template"])),
            ("core.fsmonitor", "false"),
            ("core.attributesFile", str(empty_attributes)),
            ("credential.helper", ""),
            ("core.askPass", ""),
        ),
    )
    return run_env


def _is_controlled_env_name(upper_key: str) -> bool:
    return (
        upper_key in _CONTROLLED_ENV_KEYS
        or upper_key in _EXECUTION_CONTROL_ENV_NAMES
        or upper_key.startswith("GIT_")
        or upper_key.startswith("DYLD_")
    )


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
            "validation_command": _safe_text(self.validation_command),
            "base_ref": self.base_ref,
            "localized": self.localized,
            "retargeted_to": self.retargeted_to,
            "detail": _safe_text(self.detail),
        }


def _skipped(command: str, detail: str) -> FusionAttestation:
    return FusionAttestation(
        status="skipped",
        pre_patch_fired=False,
        post_patch_cleared=False,
        patch_applied=False,
        pre_exit=None,
        post_exit=None,
        validation_command=_safe_text(command),
        base_ref="",
        localized=False,
        detail=_safe_text(detail),
    )


def _error(command: str, base_ref: str, detail: str) -> FusionAttestation:
    return FusionAttestation(
        status="error",
        pre_patch_fired=False,
        post_patch_cleared=False,
        patch_applied=False,
        pre_exit=None,
        post_exit=None,
        validation_command=_safe_text(command),
        base_ref=base_ref,
        localized=False,
        detail=_safe_text(detail),
    )


def _git(
    git_executable: Path,
    cwd: Path,
    args: Sequence[str],
    run_env: dict[str, str],
    *,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the already-resolved Git binary under the closed setup env."""
    input_bytes = input_text.encode("utf-8") if input_text is not None else None
    stdin = None if input_bytes is not None else subprocess.DEVNULL
    try:
        raw_proc = subprocess.run(
            [str(git_executable), *args],
            cwd=str(cwd),
            input=input_bytes,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            close_fds=True,
            timeout=_GIT_SETUP_TIMEOUT_S,
            env=run_env,
        )
    except subprocess.TimeoutExpired as exc:
        operation = str(args[0]) if args else "command"
        raise RuntimeError(f"trusted git {operation} timed out") from exc
    proc = subprocess.CompletedProcess(
        raw_proc.args,
        raw_proc.returncode,
        raw_proc.stdout.decode("utf-8", "replace"),
        raw_proc.stderr.decode("utf-8", "replace"),
    )
    if check and proc.returncode != 0:
        operation = str(args[0]) if args else "command"
        detail = _safe_text(_tail(proc.stderr.strip())) or f"exit {proc.returncode}"
        raise RuntimeError(f"trusted git {operation} failed: {detail}")
    return proc


def _resolve_head(git_executable: Path, repo: Path, run_env: dict[str, str]) -> str:
    head = _git(git_executable, repo, ("rev-parse", "--verify", "HEAD^{commit}"), run_env).stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", head):
        raise RuntimeError("trusted git returned a malformed HEAD object id")
    return head.lower()


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


def _apply_patch(
    git_executable: Path,
    worktree: Path,
    patch_text: str,
    run_env: dict[str, str],
) -> tuple[bool, str]:
    """Apply a unified diff inside the worktree via ``git apply`` (text-only)."""
    if not patch_text.strip():
        return False, "empty candidate_patch"
    proc = _git(
        git_executable,
        worktree,
        ("apply", "--whitespace=nowarn", "--", "-"),
        run_env,
        check=False,
        input_text=patch_text if patch_text.endswith("\n") else patch_text + "\n",
    )
    if proc.returncode != 0:
        return False, _safe_text(_tail(proc.stderr.strip())) or "git apply failed"
    return True, "applied"


def _materialize_pristine_worktree(
    *,
    git_executable: Path,
    source_repo: Path,
    destination: Path,
    base_ref: str,
    run_env: dict[str, str],
) -> None:
    """Create a config-clean, object-independent checkout of ``base_ref``.

    Clone never checks files out, so source hooks and filters cannot run. The
    generated clone has no copied source config; global/system config and Git
    templates are disabled by ``run_env`` before the detached checkout.
    """
    _git(
        git_executable,
        destination.parent,
        (
            "clone",
            "--quiet",
            "--local",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(source_repo),
            str(destination),
        ),
        run_env,
    )

    alternates = destination / ".git" / "objects" / "info" / "alternates"
    if alternates.exists():
        raise RuntimeError("pristine replay clone unexpectedly shares an object store")

    filter_config = _git(
        git_executable,
        destination,
        ("config", "--local", "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$"),
        run_env,
        check=False,
    )
    if filter_config.returncode not in {0, 1}:
        raise RuntimeError("cannot inspect replay clone filter configuration")
    if filter_config.stdout.strip():
        raise RuntimeError("replay clone contains executable filter configuration")

    _git(
        git_executable,
        destination,
        ("checkout", "--detach", "--quiet", base_ref),
        run_env,
    )
    _git(git_executable, destination, ("remote", "remove", "origin"), run_env)
    _git(
        git_executable,
        destination,
        ("reflog", "expire", "--expire=now", "--all"),
        run_env,
    )
    # Clone metadata can retain the source path even after removing origin.
    # It is irrelevant to replay and would give validation a locator for the
    # real repository, so remove it before any consumer process starts.
    shutil.rmtree(destination / ".git" / "logs", ignore_errors=True)
    try:
        (destination / ".git" / "FETCH_HEAD").unlink()
    except FileNotFoundError:
        pass

    checked_out = _resolve_head(git_executable, destination, run_env)
    if checked_out != base_ref:
        raise RuntimeError("pristine replay clone did not resolve to the requested HEAD")
    status = _git(
        git_executable,
        destination,
        ("status", "--porcelain=v1", "--untracked-files=all"),
        run_env,
    )
    if status.stdout:
        raise RuntimeError("pristine replay clone contains tracked or untracked mutations")


def _environment_value_is_sensitive(value: str) -> bool:
    if "\x00" in value or len(value) > 32_768 or _CREDENTIAL_URL_RE.search(value):
        return True
    _redacted, had_secret = redact_secrets_in_string(value)
    return had_secret


def _build_validation_env(
    cwd: Path,
    runtime_root: Path,
    env: dict[str, str] | None,
    protected_repo: Path,
) -> dict[str, str]:
    """Build a minimal environment with no ambient credential variables.

    Only a closed set of runtime/locale variables is inherited. ``env`` is an
    explicit per-call allowlist supplied by the caller, but credential-shaped
    names and values are still dropped and isolation controls cannot be
    overridden.
    """
    run_env = _base_child_env(
        excluded_roots=(protected_repo, cwd, runtime_root),
        include_path=True,
    )

    for raw_key, raw_value in (env or {}).items():
        key = str(raw_key)
        value = str(raw_value)
        upper_key = key.upper()
        if (
            not _ENV_NAME_RE.fullmatch(key)
            or _is_controlled_env_name(upper_key)
            or _is_sensitive_name(key)
            or _environment_value_is_sensitive(value)
        ):
            continue
        run_env[upper_key if os.name == "nt" else key] = value

    layout = _runtime_layout(runtime_root)
    empty_config = runtime_root / "empty.gitconfig"
    empty_attributes = runtime_root / "empty.gitattributes"
    pip_config = runtime_root / "pip.conf"
    npm_config = runtime_root / "npmrc"
    for empty_file in (empty_config, empty_attributes, pip_config, npm_config):
        empty_file.touch(exist_ok=True)

    run_env.update(
        {
            "HOME": str(layout["home"]),
            "USERPROFILE": str(layout["home"]),
            "APPDATA": str(layout["appdata"]),
            "LOCALAPPDATA": str(layout["appdata"]),
            "XDG_CONFIG_HOME": str(layout["config"]),
            "XDG_CACHE_HOME": str(layout["cache"]),
            "XDG_DATA_HOME": str(layout["data"]),
            "XDG_STATE_HOME": str(layout["state"]),
            "XDG_RUNTIME_DIR": str(layout["runtime"]),
            "TMPDIR": str(layout["tmp"]),
            "TMP": str(layout["tmp"]),
            "TEMP": str(layout["tmp"]),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "GIT_CONFIG_SYSTEM": str(empty_config),
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": str(layout["pycache"]),
            "PIP_CONFIG_FILE": str(pip_config),
            "PIP_CACHE_DIR": str(layout["pip-cache"]),
            "NPM_CONFIG_USERCONFIG": str(npm_config),
            "NPM_CONFIG_CACHE": str(layout["npm-cache"]),
            "YARN_CACHE_FOLDER": str(layout["yarn-cache"]),
            "UV_CACHE_DIR": str(layout["uv-cache"]),
            "CARGO_HOME": str(layout["cargo-home"]),
            "RUSTUP_HOME": str(layout["rustup-home"]),
            "GRADLE_USER_HOME": str(layout["gradle-home"]),
            "GOCACHE": str(layout["go-cache"]),
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    _apply_git_config_overrides(
        run_env,
        (
            ("credential.helper", ""),
            ("core.askPass", ""),
            ("core.hooksPath", str(layout["hooks"])),
            ("core.fsmonitor", "false"),
            ("core.attributesFile", str(empty_attributes)),
        ),
    )
    return run_env


def _read_output_tail(output) -> str:
    output.flush()
    output.seek(0, os.SEEK_END)
    size = output.tell()
    output.seek(max(0, size - _OUTPUT_TAIL))
    text = output.read(_OUTPUT_TAIL).decode("utf-8", "replace")
    # Validation output is currently not surfaced in attestations, but keep
    # this private return value scrubbed for direct/internal callers as well.
    return _safe_text(text)


def _create_windows_kill_job() -> int | None:
    """Create a Job Object whose members die when the last handle closes."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(handle)
            return None
        return int(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _assign_windows_kill_job(handle: int, proc: subprocess.Popen) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        return bool(kernel32.AssignProcessToJobObject(wintypes.HANDLE(handle), wintypes.HANDLE(int(proc._handle))))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _close_windows_job_handle(handle: int | None) -> bool:
    if not handle:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        return bool(kernel32.CloseHandle(wintypes.HANDLE(handle)))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _close_windows_kill_job(proc: subprocess.Popen) -> bool:
    handle = getattr(proc, "_roam_kill_job", None)
    if not handle:
        return False
    setattr(proc, "_roam_kill_job", None)
    return _close_windows_job_handle(handle)


def _terminate_process_tree(proc: subprocess.Popen) -> bool:
    """Force-terminate the validation process group/tree and wait for root."""
    if os.name == "nt" and _close_windows_kill_job(proc):
        try:
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return True
    if proc.poll() is not None:
        return True

    tree_kill_succeeded = False
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
        system32 = Path(system_root) / "System32"
        taskkill = system32 / "taskkill.exe"
        taskkill_argv = [str(taskkill if taskkill.exists() else "taskkill.exe"), "/PID", str(proc.pid), "/T", "/F"]
        try:
            killed = subprocess.run(
                taskkill_argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
                env={"SYSTEMROOT": system_root, "WINDIR": system_root, "PATH": str(system32)},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            tree_kill_succeeded = killed.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            tree_kill_succeeded = False
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            tree_kill_succeeded = True
        except ProcessLookupError:
            tree_kill_succeeded = True
        except OSError:
            tree_kill_succeeded = False

    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return tree_kill_succeeded


def _run_validation(
    cwd: Path,
    command: ValidationCommand,
    timeout: float,
    env: dict[str, str] | None,
    *,
    runtime_root: Path,
    protected_repo: Path,
) -> tuple[int, str]:
    argv = _normalize_validation_argv(command)
    if not argv:
        raise ValueError("validation command is empty")
    run_env = _build_validation_env(cwd, runtime_root, env, protected_repo)
    popen_kwargs: dict[str, Any] = {}
    process_argv = argv
    job_handle: int | None = None
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        process_argv = [os.path.realpath(os.sys.executable), "-c", _WINDOWS_GATE_WRAPPER, *argv]
        job_handle = _create_windows_kill_job()
        if job_handle is None:
            raise RuntimeError("Windows process-tree containment is unavailable")
    else:
        popen_kwargs["start_new_session"] = True

    proc: subprocess.Popen | None = None
    with tempfile.TemporaryFile(mode="w+b") as output:
        try:
            proc = subprocess.Popen(
                process_argv,
                cwd=str(cwd),
                shell=False,
                stdin=subprocess.PIPE if os.name == "nt" else subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                close_fds=True,
                env=run_env,
                **popen_kwargs,
            )
            if job_handle is not None:
                if not _assign_windows_kill_job(job_handle, proc):
                    _close_windows_job_handle(job_handle)
                    job_handle = None
                    _terminate_process_tree(proc)
                    raise RuntimeError("Windows validation process could not enter its kill-on-close job")
                setattr(proc, "_roam_kill_job", job_handle)
                job_handle = None
                assert proc.stdin is not None
                proc.stdin.write(b"1")
                proc.stdin.close()
            try:
                returncode = proc.wait(timeout=float(timeout))
            except subprocess.TimeoutExpired as exc:
                setattr(exc, "process_tree_terminated", _terminate_process_tree(proc))
                raise
            _close_windows_kill_job(proc)
            return returncode, _read_output_tail(output)
        except BaseException:
            if proc is not None and proc.poll() is None:
                _terminate_process_tree(proc)
            if job_handle is not None:
                _close_windows_job_handle(job_handle)
            raise


def run_replay_gate(
    consumer_repo: str | Path,
    candidate_patch: str,
    validation_command: ValidationCommand | None,
    *,
    retarget_file: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    env: dict[str, str] | None = None,
) -> FusionAttestation:
    """Certify (or refute) a defect transfer in a throwaway worktree.

    Propose-only: never mutates the real tree, never commits, never pushes.
    A string command is parsed once for CLI compatibility; callers that can
    should pass a sequence of argv elements. There is no implicit shell.

    This function provides environment and process-tree containment, not a
    filesystem/network sandbox. Use an external sandbox for hostile code.
    """
    raw_command_label = _display_command_input(validation_command)
    if validation_command is None or (isinstance(validation_command, str) and not validation_command.strip()):
        return _skipped(
            raw_command_label,
            "replay skipped: provide the consumer's own --validation-command to certify",
        )
    try:
        validation_argv = _normalize_validation_argv(validation_command)
    except (OSError, ValueError) as exc:
        return _error(raw_command_label, "", f"invalid validation argv: {_safe_text(exc)}")
    if not validation_argv:
        return _skipped(
            raw_command_label,
            "replay skipped: provide the consumer's own --validation-command to certify",
        )
    command_label = _display_validation_argv(validation_argv)
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        return _error(command_label, "", "validation timeout must be a positive number of seconds")
    if not math.isfinite(timeout_value) or timeout_value <= 0 or timeout_value > MAX_TIMEOUT_S:
        return _error(
            command_label,
            "",
            f"validation timeout must be between 0 and {MAX_TIMEOUT_S} seconds",
        )

    patch_to_apply = candidate_patch
    retargeted_to: str | None = None
    if retarget_file:
        retargeted = retarget_patch(candidate_patch, retarget_file)
        if retargeted is not None:
            patch_to_apply = retargeted
            retargeted_to = retarget_file.replace("\\", "/").lstrip("/")

    repo_input = Path(consumer_repo).resolve(strict=False)
    if not repo_input.is_dir():
        return _error(command_label, "", f"not a git repository: {repo_input}")

    temporary_roots = [
        Path(tempfile.mkdtemp(prefix="roam-spn-control-")),
        Path(tempfile.mkdtemp(prefix="roam-spn-pre-")),
        Path(tempfile.mkdtemp(prefix="roam-spn-post-")),
    ]
    for root in temporary_roots:
        if os.name != "nt":
            os.chmod(root, 0o700)
    control_root, pre_root, post_root = temporary_roots
    pre_worktree = pre_root / "worktree"
    post_worktree = post_root / "worktree"
    base_ref = ""
    try:
        git_executable = _resolve_trusted_git(repo_input)
        control_git_env = _build_git_env(control_root / "git-runtime", git_executable, repo_input)
        repo_probe = _git(
            git_executable,
            repo_input,
            ("rev-parse", "--show-toplevel"),
            control_git_env,
            check=False,
        )
        if repo_probe.returncode != 0:
            return _error(command_label, "", f"not a git repository: {repo_input}")
        top_level = repo_probe.stdout.strip()
        if not top_level or "\x00" in top_level or "\n" in top_level:
            return _error(command_label, "", "trusted git returned a malformed repository root")
        repo = Path(top_level).resolve(strict=True)
        if not _is_within(repo, repo_input):
            return _error(command_label, "", "trusted git returned an unrelated repository root")
        if _usable_git_executable(git_executable, repo) != git_executable:
            return _error(command_label, "", "resolved Git executable is inside the repository boundary")

        control_git_env = _build_git_env(control_root / "git-runtime", git_executable, repo)
        base_ref = _resolve_head(git_executable, repo, control_git_env)
        pre_git_env = _build_git_env(pre_root / "git-runtime", git_executable, repo)
        post_git_env = _build_git_env(post_root / "git-runtime", git_executable, repo)

        # Both snapshots exist and are proven pristine before PRE gets any CPU.
        # A pre-check cannot seed post through tracked files, untracked files,
        # Git object/config state, or phase-local HOME/cache/runtime state.
        _materialize_pristine_worktree(
            git_executable=git_executable,
            source_repo=repo,
            destination=pre_worktree,
            base_ref=base_ref,
            run_env=pre_git_env,
        )
        _materialize_pristine_worktree(
            git_executable=git_executable,
            source_repo=repo,
            destination=post_worktree,
            base_ref=base_ref,
            run_env=post_git_env,
        )

        pre_exit, _pre_out = _run_validation(
            pre_worktree,
            validation_argv,
            timeout_value,
            env,
            runtime_root=pre_root / "validation-runtime",
            protected_repo=repo,
        )
        pre_patch_fired = pre_exit != 0
        if not pre_patch_fired:
            return FusionAttestation(
                status="not_applicable",
                pre_patch_fired=False,
                post_patch_cleared=False,
                patch_applied=False,
                pre_exit=pre_exit,
                post_exit=None,
                validation_command=command_label,
                base_ref=base_ref,
                localized=False,
                detail="predicate did not fire pre-patch: the defect is not present at this site",
                retargeted_to=retargeted_to,
            )

        applied, apply_detail = _apply_patch(
            git_executable,
            post_worktree,
            patch_to_apply,
            post_git_env,
        )
        if not applied:
            return FusionAttestation(
                status="patch_failed",
                pre_patch_fired=True,
                post_patch_cleared=False,
                patch_applied=False,
                pre_exit=pre_exit,
                post_exit=None,
                validation_command=command_label,
                base_ref=base_ref,
                localized=True,
                detail=f"defect fired but candidate_patch did not apply: {apply_detail}",
                retargeted_to=retargeted_to,
            )

        post_exit, _post_out = _run_validation(
            post_worktree,
            validation_argv,
            timeout_value,
            env,
            runtime_root=post_root / "validation-runtime",
            protected_repo=repo,
        )
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
            validation_command=command_label,
            base_ref=base_ref,
            localized=True,
            detail=detail,
            retargeted_to=retargeted_to,
        )
    except subprocess.TimeoutExpired as exc:
        containment = (
            "process tree terminated"
            if getattr(exc, "process_tree_terminated", False)
            else "process-tree termination attempted but not confirmed"
        )
        return _error(
            command_label,
            base_ref,
            f"validation command timed out after {timeout_value:g}s; {containment}",
        )
    except Exception as exc:  # noqa: BLE001 - any orchestration failure is an honest error attestation
        return _error(command_label, base_ref, f"replay-gate error: {_safe_text(exc)}")
    finally:
        for root in temporary_roots:
            shutil.rmtree(root, ignore_errors=True)
