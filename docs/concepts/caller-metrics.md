# Caller metrics — the four "callers" counts

roam-code exposes several commands that report how many callers a symbol
has. They do not all compute the same number. A single highly-imported
symbol such as `useThemeClasses` returns 528 from one command, 269 from
another, 264 from a third, and 360 from a fourth — and every number is
correct, because each command answers a subtly different question.

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

Granularity (finest to coarsest): `raw_edge_rows` > `distinct_caller_tuples` > `direct_in_degree` > `transitive_upstream_bfs`. The first three count direct callers with different dedup strategies; the fourth counts transitive reach so it can exceed the direct counts when the graph fans out.

## When to use which

* **`raw_edge_rows`** — "How many call sites mention this symbol?" Use when you care about textual / structural occurrences. Always biggest because a function called five times in one file contributes five rows.
* **`direct_in_degree`** — "How many distinct callers does this symbol have?" Use when you want a graph-theoretic in-degree. This is the metric `roam fan`, `roam symbol`, and the `key_abstractions` block in `roam understand` all expose.
* **`distinct_caller_tuples`** — "How many production-scope callers remain after deduping by file + edge kind?" Use when you are filtering away tests and want the headline number `roam uses` reports.
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

When adding a new command that surfaces a callers count, follow the same convention: include `caller_metric_definition` in the JSON summary so consumers can interpret the number without re-reading source.
