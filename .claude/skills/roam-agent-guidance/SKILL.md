---
name: roam-agent-guidance
description: "Review and maintain the technical instructions Roam gives coding agents: shipped skills, tool descriptions, preset guidance and generated instruction blocks. Use for stale, unreachable or misleading usage guidance, not product persuasion, general repo exploration or runtime defect repair."
---

# Shared project skill

The canonical body lives at `.agents/skills/roam-agent-guidance/SKILL.md` in this repo (the cross-agent
location Codex reads natively). **Read that file now and apply it**; resolve its relative
references against that directory. Do not maintain a second body here — this stub exists
only because Claude Code does not read `.agents/skills`.
