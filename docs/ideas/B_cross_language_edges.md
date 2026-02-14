# Idea B: Generalized Cross-Language Edge Resolution

## Origin
river-mounts fork -- Salesforce-specific cross-language edges (Apex<->LWC<->VF).
We already have this. The idea is to GENERALIZE the pattern to ANY multi-lang monorepo.

## Why
Polyglot monorepos are everywhere: Python+JS, Go+Proto, Java+Kotlin, Ruby+ERB.
Our Salesforce cross-language code is hardwired in relations.py. A plugin system
would let us support any language combination without touching core code.

## Research Findings

Industry consensus (SCIP, Kythe, Glean): shared symbol namespace + per-pattern bridges.
GitHub Stack Graphs tried a universal framework and was archived (Sep 2025) -- too complex.

### Three-Layer Architecture (inspired by SCIP + Kythe)

**Layer 1: Canonical Symbol Namespace**
Extend `qualified_name` for cross-language matching. When two languages produce
the same canonical name, edges are created by JOIN -- zero bridge code needed.

**Layer 2: Bridge Plugins**
```
src/roam/bridges/
    base.py               # CrossLanguageBridge ABC
    registry.py            # Auto-discovers bridge_*.py files
    bridge_salesforce.py   # Refactored from current relations.py
    bridge_protobuf.py     # .proto -> Go/Java/Python
    bridge_rest_api.py     # URL string matching frontend<->backend
    bridge_template.py     # Template engine -> host language
    bridge_config.py       # Config string -> code symbol
```

Each bridge implements:
- `detect(languages, files) -> bool` -- does this bridge apply?
- `resolve(source_symbols, target_symbols) -> list[Edge]` -- produce edges
- Edge kinds: `x-lang:generates`, `x-lang:calls`, `x-lang:references`

**Layer 3: Heuristic String-Literal Matching**
For the long tail: REST URLs, CSS classes, SQL tables, config refs.
Extract string literals during parsing, match against symbol index.
Low-confidence edges with configurable threshold.

### Schema Changes (minimal)
```sql
ALTER TABLE edges ADD COLUMN bridge TEXT;       -- NULL for intra-language
ALTER TABLE edges ADD COLUMN confidence REAL;   -- 0.0-1.0, NULL = 1.0
```

### Implementation Phases
1. Refactor Salesforce code into bridge_salesforce.py (no behavior change)
2. Add bridge auto-detection to indexer pipeline
3. Add bridge_protobuf.py (most common cross-lang pattern)
4. Add heuristic bridges one at a time (REST API, templates, config)
5. New `roam x-lang` command showing cross-language boundaries

### Key Design Principles
- Mirrors existing LanguageExtractor plugin pattern
- Bridges auto-detect applicability (zero config)
- No new dependencies (tree-sitter + string matching)
- Incremental and non-breaking (each bridge ships independently)

## Priority: STRATEGIC (Tier 1)
## Effort: High (phased over multiple releases)
## Files touched: New bridges/ dir, relations.py refactor, schema.py, indexer.py
