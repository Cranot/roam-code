# Agent Code Quality Evaluation

Benchmark that measures code quality produced by AI coding agents using [roam-code](https://github.com/cosmohac/roam-code) as the evaluator.

## The Matrix

| | Claude Code | Codex | Gemini CLI |
|---|---|---|---|
| **Vanilla** (no roam) | score | score | score |
| **+ Roam CLI** commands | score | score | score |
| **+ Roam MCP** tools | score | score | score |

3 agents x 3 modes x 5 tasks = **45 evaluations**

## Tasks ("Complete" Group)

| # | Task | Language | What it builds |
|---|---|---|---|
| 1 | react-todo | JavaScript/React | Full TODO app with categories, priorities, persistence |
| 2 | astro-landing | JavaScript/Astro | SaaS landing page with all sections |
| 3 | python-crawler | Python | Async web crawler with reports |
| 4 | cpp-calculator | C++ | Expression parser with REPL |
| 5 | go-loganalyzer | Go | Concurrent log file analyzer |

## Metrics (per workspace)

| Metric | Command | What it measures |
|---|---|---|
| Health Score | `roam health` | Overall 0-100 code quality score |
| Dead Code | `roam dead` | Unused symbols left behind |
| Complexity | `roam complexity` | Cognitive complexity of functions |
| Cycles | `roam cycles` | Circular dependencies |
| Coupling | `roam coupling` | Inter-module coupling |
| Gate | `roam gate` | Pass/fail quality thresholds |

## How to Run

### 1. Export prompts

```bash
cd benchmarks/agent-eval
python run_eval.py --export-prompts
```

This creates `prompts/` with one text file per (task, mode) combination.

### 2. Run each agent

For each agent, create a workspace and give it the prompt:

```
workspaces/
  claude-code/
    react-todo_vanilla/      # Claude Code + react-todo + no roam
    react-todo_roam-cli/     # Claude Code + react-todo + roam CLI
    react-todo_roam-mcp/     # Claude Code + react-todo + roam MCP
    ...
  codex/
    react-todo_vanilla/
    ...
  gemini-cli/
    react-todo_vanilla/
    ...
```

Give each agent the corresponding prompt from `prompts/{task}_{mode}.txt`.

### 3. Evaluate

```bash
# Check status
python run_eval.py --list

# Run evaluation on all completed workspaces
python run_eval.py

# Force re-evaluation
python run_eval.py --force
```

### 4. View results

- Terminal: comparison tables printed automatically
- HTML: `results/report.html`
- Raw JSON: `results/{agent}_{task}_{mode}.json`

## Modes Explained

- **vanilla**: The agent gets only the task prompt. No roam tools.
- **roam-cli**: Same prompt + instructions to use `roam` CLI commands after building to validate and fix quality issues.
- **roam-mcp**: Same prompt + roam available as MCP tools throughout development for continuous quality feedback.

The key question: **Does access to roam make agents write better code?**
