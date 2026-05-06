# Awesome-list PR drafts

Held for Phase 3 launch. Each PR adds a roam-code entry to a curated list
that AI-coding teams browse when picking tools.

**Already in flight** (do not re-submit):
- `punkpeye/awesome-mcp-servers#2470`
- `appcypher/awesome-mcp-servers#431`

**To be drafted here, submitted at Phase 3:**
- `hesreallyhim/awesome-claude-code`
- `rohitg00/awesome-claude-code-toolkit`
- `VoltAgent/awesome-agent-skills`
- A fourth target list (originally `jqueryscript/awesome-claude-code` per the
  plan; verify the slug — might be a different fork or merged elsewhere).

## Pre-submit checklist

For each PR:

- [ ] Verify the target repo is still active (last commit < 90 days; PRs being
      merged).
- [ ] Read the contributing guidelines if present (alphabetical ordering,
      formatting conventions, badge requirements).
- [ ] Fork the repo and create a branch named `add-roam-code`.
- [ ] Verify the link to roam-code main README renders cleanly (no MIT-license
      stragglers — confirmed clean post-12.23).
- [ ] Submit small, focused PR — only the new entry, no other changes. Avoid
      "while I'm here, fix this typo" scope creep that tanks merge rate.

## Submit timing

All four PRs go out within the same 4-hour window as Show HN goes live (Sun
22:00 PT / Mon 08:00 CET). Don't submit days earlier — maintainers see them
in their email and the PR + Show HN double-up reinforces signal.

---

## PR #1 — hesreallyhim/awesome-claude-code

- **Repo**: <https://github.com/hesreallyhim/awesome-claude-code>
- **Branch**: `add-roam-code`
- **Section**: most likely `## Tools` or `## CLI Tools` — locate by reading
  the README structure first; ordering is usually alphabetical within the
  section.

### Proposed entry (append in alphabetical order)

```markdown
- **[roam-code](https://github.com/Cranot/roam-code)** — Local code-graph CLI
  + MCP server (179 commands, 128 MCP tools, 27 languages) that gives Claude
  Code instant codebase comprehension. Pre-indexes symbols, call graphs,
  dependencies, architecture layers, and git history into local SQLite. 100%
  local, zero API keys, Apache 2.0.
```

### PR title

`Add roam-code — local code-graph CLI + MCP server for Claude Code`

### PR body

```markdown
This PR adds [roam-code](https://github.com/Cranot/roam-code) to the list.

**What it does**: Pre-indexes a codebase into a local SQLite graph (symbols,
call graphs, dependencies, architecture layers, git history) and exposes 178
commands + 128 MCP tools so Claude Code can answer architecture questions
(blast radius, dead code, ownership, cycles, layer violations) without
re-grepping files every turn.

**Why it fits the list**:
- Designed specifically for Claude Code workflow (`roam describe --agent-prompt`
  produces a CLAUDE.md drop-in; `roam pr-risk` integrates with PR review).
- 100% local, zero API keys — no data leaves the user's machine.
- Apache 2.0, in active development (released 12.25 this week).

I'm the author. Happy to revise the entry to match list conventions if needed.
```

---

## PR #2 — rohitg00/awesome-claude-code-toolkit

- **Repo**: <https://github.com/rohitg00/awesome-claude-code-toolkit>
- **Branch**: `add-roam-code`
- **Section**: locate the most appropriate section for "code intelligence"
  / "MCP servers". Likely `## Code Intelligence` or `## Workflow Tools`.

### Proposed entry

```markdown
- **[roam-code](https://github.com/Cranot/roam-code)** — Code-graph CLI and
  MCP server that gives Claude Code architectural context. Indexes symbols,
  edges, ownership, and git history into a local SQLite. 179 commands, 128
  MCP tools, 27 languages, deterministic and offline. Apache 2.0.
```

### PR title

`Add roam-code to code intelligence section`

### PR body

(Reuse PR #1 body with the section name swapped.)

---

## PR #3 — VoltAgent/awesome-agent-skills

- **Repo**: <https://github.com/VoltAgent/awesome-agent-skills>
- **Branch**: `add-roam-code-skill`
- **Section**: probably under skills / tools categories that are agent-facing.

### Proposed entry

```markdown
- **[roam-code](https://github.com/Cranot/roam-code)** — A code-intelligence
  skill any agent can install via MCP. Computes blast radius, dead code, PR
  risk, architecture map, and bus-factor risks on a local SQLite graph. 27
  languages, 100% local, no API keys. Designed for agentic coding workflows.
```

### PR title

`Add roam-code — code-intelligence skill for agentic workflows`

### PR body

(Reuse with emphasis on the agent-skill angle: this is a passive
skill-shaped MCP server that any agent — Claude Code, Codex CLI, Cursor —
can plug into.)

---

## PR #4 — Fourth-list target (placeholder)

The plan named `jqueryscript/awesome-claude-code` — verify this slug. As of
2026-05-05 the most active alternative awesome-claude-code lists are:

- <https://github.com/zebbern/awesome-claude-code> — active, growing.
- <https://github.com/ericbuess/awesome-claude-code> — older, may be stale.
- The same `hesreallyhim/awesome-claude-code` we already cover above.

Pick the most-active alternative at submit time. Use the same template as
PR #1 with the appropriate slug.

---

## After all PRs are open

- Track each in `private/awesome-list-prs.md` (PR URL, status, last update).
- Don't push for review more than once. Maintainers see the email; pushing
  twice signals impatience and reduces merge probability.
- If a maintainer asks for revisions, respond within 48 hours and keep
  edits scoped to what they asked.
- If a maintainer rejects: thank them, ask one specific question about why,
  apply the lesson to the next list submission. Don't argue.

## Anti-patterns

- **Don't add the entry to multiple sections of the same list** — looks
  spammy.
- **Don't include marketing copy** ("the best", "revolutionary", etc.) —
  awesome-list maintainers reject those instantly.
- **Don't link to roam.consulting** as the primary destination — link to
  the GitHub repo. Awesome-lists are technical surfaces; the commercial
  page lives downstream.
- **Don't create a fork-with-many-edits PR.** One PR = one entry. Other
  cleanups go in separate PRs (or not at all — not your repo).
