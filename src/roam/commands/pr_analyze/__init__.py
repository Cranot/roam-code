"""``roam.commands.pr_analyze`` — modules extracted from cmd_pr_analyze.py (D5).

The CLI command itself stays in ``cmd_pr_analyze.py`` because LazyGroup
expects ``cmd_*.py`` modules to expose a Click command. The pure helpers
that don't depend on Click live here so the file at the registration
site stays small enough to read end-to-end.

Re-exports are kept in cmd_pr_analyze.py so existing tests and callers
that import private helpers (``_check_rules``, ``_cache_key`` etc.) keep
working without churn.
"""

from __future__ import annotations
