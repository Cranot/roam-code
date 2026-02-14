# Idea E: Dead Code Aging + Effort Estimation

## Origin
chuckjewell fork -- dead code grouping. We have --by-directory, --by-kind, --clusters.
The idea is to add TIME-BASED analysis: how long has code been dead? How hard to remove?

## Why
Current `roam dead` says WHAT is dead. It doesn't say HOW LONG it's been dead or
HOW MUCH EFFORT to remove it. Time-based priority makes dead code actionable.

## Research Findings

### Academic Backing
- Typical codebases: 5-10% dead code (Romano et al., IEEE TSE 2018)
- Dead code rarely revives (Kaplan-Meier survival analysis confirms)
- Code removal cost compounds over time (knowledge loss, dependency growth)
- Knight Capital lost $440M from reactivated dead code (2012)
- Meta's SCARF deleted 100M+ lines across 370K automated PRs

### Staleness Metrics (all data already in our DB)

| Metric | Source | Notes |
|--------|--------|-------|
| age_days | git_stats.get_symbol_blame() | Oldest blame line |
| last_modified_days | git_stats.get_symbol_blame() | Newest blame line |
| author_active | git_commits | Author has commits in last 90 days? |
| dead_loc | symbols.line_end - line_start + 1 | Already stored |
| cognitive_complexity | symbol_metrics table | Already stored |
| cluster_size | _find_dead_clusters() | Already computed |
| importing_files | file_edges | Already queryable |

### Decay Score Formula (0-100)
```python
def decay_score(age_days, cognitive_complexity, cluster_size,
                importing_files, author_active, dead_loc):
    age_points = min(35, 7 * math.log2(1 + age_days / 90))
    cc_points = min(25, cognitive_complexity * 1.5)
    coupling_points = min(20, importing_files * 2 + cluster_size * 3)
    size_points = min(10, dead_loc / 20)
    author_points = 0 if author_active else 10
    return min(100, age_points + cc_points + coupling_points + size_points + author_points)
```

Interpretation:
- 0-25: **Fresh** -- easy to remove, author likely available
- 26-50: **Stale** -- needs investigation
- 51-75: **Decayed** -- significant effort, no institutional knowledge
- 76-100: **Fossilized** -- deeply embedded, complex, authors gone

### Effort Estimation Formula
```python
def estimate_removal_minutes(dead_loc, cognitive_complexity,
                              importing_files, cluster_size,
                              age_years, author_active):
    base = dead_loc * 1.0                                    # 1 min/line
    complexity_factor = 1.0 + (cognitive_complexity / 20.0)
    coupling_factor = 1.0 + (0.05 * importing_files) + (0.1 * max(0, cluster_size - 1))
    age_factor = 1.0 + (0.1 * min(age_years, 10))
    author_factor = 0.8 if author_active else 1.0
    return base * complexity_factor * coupling_factor * age_factor * author_factor
```

### CLI Interface
```bash
roam dead --aging           # Add age/staleness columns
roam dead --effort          # Add effort estimation
roam dead --decay           # Show decay score + distribution
roam dead --sort-by-age     # Oldest-first
roam dead --sort-by-effort  # Highest effort first
roam dead --sort-by-decay   # Most fossilized first
```

### JSON Extension
```json
{
  "summary": {
    "verdict": "78 dead exports",
    "total_dead_loc": 1240,
    "total_effort_hours": 24.5,
    "median_age_days": 342,
    "decay_distribution": {"fresh": 12, "stale": 28, "decayed": 25, "fossilized": 13}
  }
}
```

### Performance Note
Only slow operation: `git blame` per file. Mitigate by blaming each file ONCE
and extracting ranges for all dead symbols in that file. Existing `get_blame_for_file()`
already returns per-line data.

## Priority: MEDIUM (Tier 2)
## Effort: Medium
## Files touched: cmd_dead.py (extend), possibly new index/staleness.py
