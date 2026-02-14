# Idea C: Health Trend Anomaly Detection + Forecasting

## Origin
chuckjewell fork -- snapshot/trend with CI assertions. We have sparklines + --assert.
The idea is to go DEEPER: statistical anomaly detection, forecasting, pattern alerts.

## Why
Current `roam trend` shows sparklines and enforces thresholds. But it can't tell you
"cycles are creeping up" or "health score regime changed at snapshot 12" or
"at current rate, dead_exports will exceed 50 in 8 snapshots."

## Research Findings

Key constraint: small datasets (10-100 snapshots). Eliminates ML, ARIMA, deep learning.
All recommended algorithms use Python stdlib only (math, statistics).

### Tier 1 Algorithms (Highest Value, ~80 lines total)

**Modified Z-Score (MAD)** -- Point anomaly detection
```
median = median(values)
MAD = median(|x_i - median|)
z_i = 0.6745 * (x_i - median) / MAD
anomaly if |z_i| > 3.5
```
- 15 lines, works at n >= 5, O(n log n)
- Output: `ANOMALY: cycles=12 (z=4.2, typical range 2-6)`

**Theil-Sen Robust Slope** -- Trend estimation
```
slope = median of all (y_j - y_i) / (j - i) pairs
```
- 20 lines, works at n >= 4, O(n^2) but n<=100 = instant
- Output: `TREND: dead_exports increasing +3.2/snapshot (p=0.003)`

**Mann-Kendall Test** -- Trend significance
- 30 lines, uses only math.erf, works at n >= 8
- Output: `FORECAST: dead_exports will exceed 50 in ~8 snapshots`

**Western Electric Rules** -- Pattern-based early warning
- 50 lines, sliding window, no deps
- Output: `WARNING: cycles rising 6 consecutive snapshots (Rule 5)`

### Tier 2 (Medium complexity)

**CUSUM** -- Slow degradation detection (20 lines)
**Delta-based analysis** -- Rate-of-change for growth metrics

### Metric Classification
- **Growth metrics** (files, symbols, edges): detect anomalous growth RATE
- **Quality metrics** (cycles, dead, violations): detect upward trends
- **Composite** (health_score): detect regime changes via CUSUM

### CLI Interface
```bash
roam trend                       # Existing: sparklines + table
roam trend --anomalies           # Flag anomalous metrics
roam trend --forecast            # Theil-Sen slope + forecast
roam trend --analyze             # Full: anomalies + trends + patterns
roam trend --fail-on-anomaly     # CI: exit 1 on anomaly
roam trend --sensitivity=medium  # low/medium/high (sigma thresholds)
roam trend --assert "delta_cycles<=2,trend_cycles<=0"  # Delta assertions
```

### JSON Output
```json
{
  "command": "trend",
  "summary": {"verdict": "degrading", "anomaly_count": 2, "trend_direction": "declining"},
  "anomalies": [{"metric": "cycles", "value": 12, "z_score": 4.2, "typical": "2-6"}],
  "trends": [{"metric": "dead_exports", "slope": 3.2, "p_value": 0.003, "direction": "increasing"}],
  "forecasts": [{"metric": "dead_exports", "target": 50, "snapshots_until": 8}],
  "patterns": [{"metric": "cycles", "rule": 5, "description": "6 consecutive increases"}]
}
```

### Key: Zero New Dependencies
All algorithms implementable with Python stdlib (math, statistics, collections).
Total computation: under 10ms for n=100 snapshots.

## Priority: HIGH (Tier 1)
## Effort: Medium (single focused session)
## Files touched: cmd_trend.py (extend), possibly new graph/anomaly.py module
