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
