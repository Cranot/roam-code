# Go Log Analyzer

Concurrent CLI log analyzer for Apache Combined, Nginx, and JSON Lines logs.

## Features

- Accepts files and directories as input
- Recursively scans directories for log files
- Auto-detects line format per file:
  - Apache Combined
  - Nginx
  - JSON Lines
- Concurrent processing using worker pool + goroutines
- Per-file and aggregate statistics:
  - Total requests
  - Unique IPs and endpoints
  - Status code class distribution (`2xx`, `3xx`, `4xx`, `5xx`, `other`) with percentages
  - Top 10 IPs
  - Top 10 endpoints
  - Top 10 slowest requests (when response time is present)
  - Requests-per-hour histogram
  - Error rate over time with spike detection
- Filters:
  - Date range (`--from`, `--to`)
  - Status range (`--status-min`, `--status-max`)
  - Endpoint regex (`--endpoint-regex`)
  - IP whitelist/blacklist (`--ip-whitelist`, `--ip-blacklist`)
- Output formats:
  - Text tables (default)
  - JSON
  - CSV
- Progress bar for long-running processing
- Graceful malformed-line handling with skipped-line counts

## Build

```bash
go build -o loganalyzer ./cmd/loganalyzer
```

## Usage

```bash
./loganalyzer [flags] <file-or-dir> [more paths...]
```

### Common flags

- `--output text|json|csv` (default: `text`)
- `--workers N` (default: number of CPUs)
- `--no-progress` disable progress bar
- `--from <timestamp>` start filter (`RFC3339`, `YYYY-MM-DD`, or `YYYY-MM-DD HH:MM:SS`)
- `--to <timestamp>` end filter
- `--status-min <code>` minimum status code (default: `0`)
- `--status-max <code>` maximum status code (default: `999`)
- `--endpoint-regex <regex>`
- `--ip-whitelist ip1,ip2,...`
- `--ip-blacklist ip1,ip2,...`

### Examples

Analyze a directory recursively:

```bash
./loganalyzer ./logs
```

Analyze multiple paths with filters:

```bash
./loganalyzer \
  --from 2025-10-01 \
  --to 2025-10-31 \
  --status-min 400 \
  --status-max 599 \
  --endpoint-regex '^/api/' \
  --ip-blacklist 10.0.0.5,10.0.0.6 \
  ./access.log ./nginx-logs
```

JSON output:

```bash
./loganalyzer --output json ./logs > report.json
```

CSV output:

```bash
./loganalyzer --output csv ./logs > report.csv
```

## Testing

Run unit tests:

```bash
go test ./...
```

Run parser benchmarks:

```bash
go test -bench . -benchmem ./internal/parser
```
