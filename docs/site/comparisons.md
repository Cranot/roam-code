# Roam vs adjacent tools — what we are and aren't

External reviewers asked us for honest comparisons against the major
tools in adjacent spaces. This page is the answer. The TL;DR is that
**Roam is a complement, not a replacement** to most of these tools.
Use the right tool for the job; sometimes that's roam, often it isn't.

## The one-liner positioning

> **Roam gives coding agents architectural sight before they edit.**

Local code graph (SQLite + tree-sitter + git history) that pre-indexes
your repo so an agent can ask "where does X live? what calls it? what
breaks if I change it? is my patch missing a clone?" in one structural
query instead of 8 grep+read cycles.

## Roam vs Cursor

**Cursor wins** on UX, distribution, and the integrated edit loop. It
indexes your codebase inside the IDE and ships embeddings out of the
box. If your team already uses Cursor and the integrated experience
is enough, you may not need roam.

**Roam wins** as a power-user companion layer for any agent — Claude
Code, Aider, Continue, your own. Local-first, no telemetry, runs
in CI, deterministic graph queries that beat embedding-only retrieval
for "who calls this?" / "what breaks?" questions.

**Use both** if you use Cursor for editing and want a separate,
auditable graph for CI gates and structural review.

## Roam vs Sourcegraph / Cody

**Sourcegraph wins** at enterprise scale — cross-repo search maturity,
SCIP-precise indexing infrastructure, organisational trust, hosted
deployment story. If you have hundreds of repos and need code search
that works across all of them, Sourcegraph is the answer.

**Roam wins** for hacker/agent workflows: local CLI, MCP-first,
offline, lightweight install, architecture simulation +
multi-agent partitioning are first-class.

**Use both** if you have an enterprise Sourcegraph instance for
cross-repo search and want a local agent-facing layer per repo.

## Roam vs CKB / CodeMCP

CKB is roam's closest direct competitor: local agent codebase
intelligence layered on SCIP + LSP + Git, with MCP tool presets.

**CKB wins** on backend cleanliness — SCIP + LSP gives precise
indexing for free. Smaller surface (76 tools, 14-active core preset).
Distributes as a Go binary / npm / Homebrew, which feels lighter for
some users.

**Roam wins** on architecture/governance (PageRank, SCC, Louvain,
Fiedler, layer detection, fitness rules), patch verification
(`roam critique`), multi-agent orchestration (`roam fleet`),
and Python-specific signal (19 idiom detectors, async-aware retrieve,
type-coverage health, model-class field display).

**Use either** depending on whether you want a precise SCIP/LSP
backend (CKB) or breadth + architecture (roam). They overlap on the
core retrieve / context / impact verbs.

## Roam vs Aider repo map

**Aider wins** on simplicity. The repo-map is invisible — Aider just
includes a token-budgeted summary of important classes/functions in
the prompt, and the user never thinks about it. One excellent
mechanism beats 152 visible commands for casual use.

**Roam wins** when the repo map isn't enough: persistent SQL graph
queries, blast-radius analysis, patch critique, architecture health,
CI gates, multi-agent partitioning. Roam exposes the structural
queries Aider keeps hidden.

**Use both**: Aider for the editing loop, roam in CI / for "before I
make this change" structural review.

## Roam vs CodeQL

**CodeQL wins** at semantic security analysis — the query language,
the vulnerability discovery workflow, the variant analysis ecosystem,
and the language models for supported languages are unmatched. Use
CodeQL for security; full stop.

**Roam complements** by making security findings agent-consumable.
``roam taint`` is a starter; we don't pretend to compete with
CodeQL's depth. Where roam adds value is graph-context for the
findings: "the SQLi in `auth.py` is reachable from these 3 entry
points and shares a clone with `legacy_auth.py:42`".

## Roam vs Semgrep

**Semgrep wins** on security rule ecosystem, AppSec workflow, and
the CI/IDE/pre-commit integration. Semgrep is the better SAST.

**Roam complements** with structural reachability and architectural
context. ``roam taint`` runs starter packs (sqli/xss/path-traversal/
cmd-injection/deserialization) and produces OpenVEX-correct output;
not a replacement for Semgrep's rule library.

## Roam vs SonarQube

**SonarQube wins** on established CI quality gates accepted by teams,
enterprise reporting, language coverage, and the management workflow.

**Roam wins** for agent-native output and graph queries. Roam's
``roam health`` gives a 0-100 score with structural signals (tangle,
god components, bottlenecks, layer violations); SonarQube ships the
same kind of metric with stronger management ergonomics.

**Use both** if you have an existing SonarQube setup and want a
local agent-facing CLI that complements it.

## Roam vs CodeScene

**CodeScene wins** on socio-technical analysis maturity, hotspot
visualisations, and the management-facing prioritisation. CodeScene
is the better debt-prioritisation platform.

**Roam wins** for local CLI + agent integration. ``roam weather`` /
``roam diagnose`` / ``roam debt`` cover the hotspot-by-churn-x-complexity
territory at command-line speed; CodeScene wins the dashboarding.

## Roam vs Codebase-Memory

Codebase-Memory is a research-backed tree-sitter knowledge graph via
MCP, parsing 66 languages, with a published evaluation across 31
repos showing 83% answer quality vs 92% for full file exploration
at 10× fewer tokens.

**Roam wins** on product surface — PyPI package, broader CLI, CI/SARIF
hooks, architecture-specific commands, patch verifier.

**Codebase-Memory wins** on benchmark rigour. We owe the community
the same kind of public, reproducible eval. Tracked as
``REV3: Accuracy & Benchmarks page`` in our action log.

## Roam vs Claude Context / vector-codebase tools

**Vector tools win** for fuzzy natural-language search ("show me
things conceptually similar to authentication"). Embeddings beat
graphs for intent-shaped queries.

**Roam wins** for deterministic structural queries. "Who calls this?"
/ "What breaks if I rename it?" / "Which clones exist of this code?"
are graph questions, not embedding questions.

**Use both** if you want both modalities. Roam doesn't ship its own
vector search; pair it with Claude Context, sqlite-vec, or any
embedding store.

---

## Where roam is the right tool

* You run agents (Claude Code, Aider, Continue, your own) and they
  burn tokens on grep+read cycles.
* You need a CI guardrail that catches "agent edited X but missed
  the clone of X in Y".
* You want architecture-level signal (cycles, layer violations,
  god components) at command-line speed without a hosted platform.
* You work in air-gapped or privacy-sensitive environments where
  cloud indexing is a non-starter.
* You're orchestrating multiple agents and need work-partitioning.

## Where roam is *not* the right tool

* Primary SAST — use CodeQL or Semgrep.
* Cross-repo search at enterprise scale — use Sourcegraph.
* Embedding-only natural-language search — use Claude Context or
  a vector database.
* Management dashboarding for tech debt — use CodeScene or SonarQube.
* If you only ever code in Cursor and the integrated experience is
  enough — you may not need anything else.

## When in doubt

Try the 5 core verbs on your repo and see if they save you time:

```bash
pip install roam-code
cd your-repo/
roam understand
roam retrieve "where is X"
roam context X
roam preflight X
git diff | roam critique
```

If they do, the rest of the surface is there when you need it. If
they don't, the right answer is probably a different tool.
