@AGENTS.md
<!--
Pointer file: Claude Code auto-loads CLAUDE.md but not AGENTS.md.
AGENTS.md is the multi-vendor source of truth — Codex/Cursor/Gemini all read it
directly, while Claude Code reads CLAUDE.md (which @-imports it here).

Per commit e5993a6 (2026-02-27), the original 263-line CLAUDE.md was removed
because it carried internal development instructions not intended for public
distribution. This 1-line @-import restores Claude Code's auto-load path
WITHOUT re-introducing the internal-content pattern.

DO NOT add content to this file. Edit AGENTS.md § "Codebase navigation with
roam" for navigation directives, and the rest of AGENTS.md for project-wide
guidance.
-->


## Bounded waits (no infinite polls)

NEVER wait with `until <cond>; do sleep N; done` or `while true; do … sleep …; done`. If the condition never becomes true (e.g. a background task you spawned died), it hangs the session forever. Always bound waits:

- `wait-for-output <file> <sentinel-regex> <timeout_sec> <producer_pid>` — exits on timeout OR if the producer process dies. Preferred for "wait for a background task's output".
- or a capped loop: `for i in $(seq 1 120); do <check> && break; sleep 5; done`.

A PreToolUse hook blocks unbounded waits; prepend `WAIT_OK=1` only if you truly need one.
