"""MCP-native enhancements for roam-code's MCP server.

Each submodule unlocks a primitive the CLI cannot have:

* ``sampling`` -- in-tool LLM calls back to the *client's* model so
  large outputs (health, repo map, briefings) can be summarised for
  the agent's current task. Preserves "100% local, zero API keys".
* ``watcher`` -- watchdog observer that pushes
  ``notifications/resources/updated`` to the client when files change,
  so the agent's view of ``roam://health`` etc. stays fresh without
  explicit polling.
* ``session`` -- per-session symbol memory. Auto-biases ranking in
  ``retrieve_context`` and ``context`` without the agent threading
  ``recent_symbols`` through every call.
* ``progress`` -- phase-aware progress reporting for long-running
  operations (init, reindex, orchestrate).
* ``completions`` -- protocol-level completion handler for prompt /
  resource-template args, plus a direct ``roam_complete`` tool the
  agent can call.

All extras are best-effort: missing dependencies (watchdog, fastmcp
versions) degrade gracefully without breaking the server.
"""

from __future__ import annotations
