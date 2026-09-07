# Caller metrics — the four "callers" counts

roam-code exposes several commands that report how many callers a symbol
has. They do not all compute the same number. A single highly-imported
symbol such as `useThemeClasses` had counts of 528, 269, 264, and 360 in the
historical example retained below. Those are not live measurements of this
checkout. Different scopes and counting rules can legitimately produce different
numbers; a disagreement still deserves inspection rather than automatic acceptance.

This document is the canonical reference. Whenever a roam command emits
a callers / fan-in / consumers / in-degree count in its JSON envelope it
also emits a `caller_metric_definition` string identifying which of the
four metrics below was used. Consumers (agents, dashboards, recipe
runners) should read that label before comparing numbers across
commands.

## The four metrics

| Metric | Definition | Source SQL essence | `useThemeClasses` |
|---|---|---|---|
| `raw_edge_rows` | Every row in `edges` whose `target_id` resolves to the symbol — preserves per-file multiplicity and counts each edge kind separately. | `SELECT COUNT(*) FROM edges WHERE target_id = ?` | 528 |
| `direct_in_degree` | Distinct upstream symbols (one row per unique source) — the precomputed `graph_metrics.in_degree` column. | `SELECT in_degree FROM graph_metrics WHERE symbol_id = ?` | 269 |
| `distinct_caller_tuples` | Distinct `(source_symbol, scope)` tuples after filtering test files / dedup by `(qualified_name, path, edge_kind)`. | `SELECT COUNT(DISTINCT (source_id, edge_kind)) FROM edges WHERE target_id = ? AND f.file_role != 'test'` | 264 |
| `transitive_upstream_bfs` | Multi-hop BFS over `edges.kind IN ('call','reference')` — counts every symbol that can reach the target within N hops. | BFS via `networkx.predecessors()` to a configurable depth (default 2 for `diagnose`). | 360 |

There is no universal numeric ordering across these metrics. The first three
count direct relationships with different filters and deduplication rules; the
fourth counts transitive reach under its own edge/depth bounds. Compare totals
only after aligning the subject, index, edge kinds, test/production scope, and
truncation. In the historical example, the production-filtered tuple count is
smaller than the unfiltered in-degree.

## When to use which

* **`raw_edge_rows`** — "How many indexed relationship rows mention this symbol?" Rows can preserve call-site multiplicity and different edge kinds, but are not a count of every textual occurrence or runtime invocation. Production filters can reduce the total. This is the metric `roam uses` reports (both `summary.total_consumers` and `summary.production_consumers`), and also what `roam context`, `roam deps`, `roam guard`, `roam invariants`, and `roam plan-refactor` expose.
* **`direct_in_degree`** — "How many distinct callers does this symbol have?" Use when you want a graph-theoretic in-degree. This is the metric `roam fan`, `roam symbol`, and the `key_abstractions` block in `roam understand` all expose.
* **`distinct_caller_tuples`** — "How many production-scope callers remain after deduping by file + edge kind?" Use when you are filtering away tests. This is the metric `roam oracle is-test-only` reports, and it is the only command that emits it. It is **not** what `roam uses` reports — `roam uses` filters tests into a separate `production_consumers` field but still counts `raw_edge_rows`, so its production count is per-call-site, not per-caller.
* **`transitive_upstream_bfs`** — "How much of the call graph depends, transitively, on this symbol?" Use when ranking root-cause suspects (`roam diagnose`) or assessing blast radius beyond direct callers.

## Which commands emit which

| Command | Field reporting callers | `caller_metric_definition` |
|---|---|---|
| `roam uses` | `summary.production_consumers` / `summary.total_consumers` | `raw_edge_rows` |
| `roam context <name>` (single + batch) | `summary.callers` / per-symbol caller array | `raw_edge_rows` |
| `roam context --for-file src/roam/cli.py` | `summary.caller_files` | `raw_edge_rows` (file granularity) |
| `roam diagnose` | `summary.upstream_count` | `transitive_upstream_bfs` |
| `roam understand` | `architecture.key_abstractions[*].fan_in` | `direct_in_degree (architecture.key_abstractions[*].fan_in)` |
| `roam oracle is-test-only` | call-site classification | `distinct_caller_tuples` |
| `roam oracle is-reachable-from-entry` | entry-to-target reach | `transitive_bfs_from_entry` |
| `roam deps` | `summary.imports` / `summary.imported_by` (file-level) | `raw_edge_rows (file-level: file_edges)` |
| `roam minimap` | embedded markdown "Touch carefully (fan-in >= N)" | `direct_in_degree (Touch carefully + file annotations)` |
| `roam fan` (mode=symbol) | `items[*].fan_in` | `direct_in_degree` |
| `roam fan` (mode=file) | `items[*].fan_in` | `direct_in_degree (file-level: distinct source files)` |
| `roam symbol` | `summary.callers` | `direct_in_degree` |
| `roam metrics` | `metrics.fan_in` (symbol or file aggregate) | `direct_in_degree (fan_in from graph_metrics.in_degree, raw_edge_rows fallback)` |
| `roam guard` | `summary.callers` (non-test callers) | `raw_edge_rows` |
| `roam invariants` | per-symbol `caller_count` | `raw_edge_rows` |
| `roam plan-refactor` | `summary.callers` (non-test callers) | `raw_edge_rows` |

When adding a new command that surfaces a callers count, follow the same convention: include `caller_metric_definition` in the JSON summary so consumers can interpret the number without re-reading source.
