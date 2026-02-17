# Agent Code Quality Evaluation

Benchmark that measures code quality produced by AI coding agents using [roam-code](https://github.com/cosmohac/roam-code) as the evaluator.

## Agents Under Test

| Agent | CLI Version | Model | Command |
|---|---|---|---|
| Claude Code (Opus) | 2.1.42 | claude-opus-4-6 | `claude --model opus --dangerously-skip-permissions -p` |
| Claude Code (Sonnet) | 2.1.42 | claude-sonnet-4-5-20250929 | `claude --model sonnet --dangerously-skip-permissions -p` |
| Codex | 0.101.0 | gpt-5.3-codex | `codex exec --dangerously-bypass-approvals-and-sandbox` |
| Gemini CLI | 0.21.3 | gemini-3-pro-preview | `gemini --yolo` |

## The Matrix

| | Claude Code Opus | Claude Code Sonnet | Codex | Gemini CLI |
|---|---|---|---|---|
| **Vanilla** (no roam) | score | score | score | score |
| **+ Roam CLI** commands | score | score | score | score |
| **+ Roam MCP** tools | score | score | score | score |

4 agents x 3 modes x 5 tasks = **60 evaluations**

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
| Coupling | `roam coupling` | Temporal coupling between files |
| Tangle Ratio | `roam health` | Circular dependency density |
| Propagation Cost | `roam health` | How far changes ripple |

## Agent Quality Score (AQS)

Composite 0-100 score combining all roam metrics:

| Category | Max Points | What it measures |
|---|---|---|
| Health | 40 | `roam health` score (0-100 scaled to 0-40) |
| Quality | 25 | Dead code, complexity, coupling penalties |
| Architecture | 15 | Tangle ratio, critical issues, file structure |
| Testing | 15 | Test file count and coverage |
| Completeness | 5 | README, build config, valid project |

**Grading:** A (90+), B (80+), C (70+), D (60+), F (<60)

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
    react-todo_vanilla/      # Claude Code Opus + react-todo + no roam
    react-todo_roam-cli/     # Claude Code Opus + react-todo + roam CLI
    ...
  claude-code-sonnet/
    react-todo_vanilla/      # Claude Code Sonnet + react-todo + no roam
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
