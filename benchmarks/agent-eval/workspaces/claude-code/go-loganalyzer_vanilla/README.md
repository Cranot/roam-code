# loganalyzer

A concurrent log file analyzer CLI tool written in Go. Supports Apache Combined, Nginx, and JSON Lines log formats with auto-detection.

## Features

- **Multi-format parsing**: Apache Combined, Nginx, JSON Lines (auto-detected)
- **Concurrent processing**: Worker pool with configurable goroutine count
- **Recursive directory scanning**: Automatically discovers log files in directories
- **Rich statistics**: Status code distribution, top IPs, top endpoints, slowest requests, hourly histograms, error rate spike detection
- **Flexible filtering**: Date range, status codes, endpoint regex, IP allow/block lists
- **Multiple output formats**: Text table (default), JSON, CSV
- **Progress bar**: Visual progress for multi-file processing
- **Malformed line handling**: Counts and reports skipped lines

## Build

```bash
go build -o loganalyzer ./cmd/loganalyzer
```

## Usage

```bash
# Analyze a single log file
./loganalyzer access.log

# Analyze all log files in a directory (recursive)
./loganalyzer /var/log/nginx/

# JSON output with 8 workers
./loganalyzer -format json -workers 8 access.log

# CSV output
./loganalyzer -format csv access.log error.log

# Filter by date range
./loganalyzer -from 2024-01-01T00:00:00Z -to 2024-01-31T23:59:59Z access.log

# Filter by status code range (4xx and 5xx only)
./loganalyzer -status-min 400 access.log

# Filter by endpoint regex
./loganalyzer -endpoint '/api/v[12]/.*' access.log

# Filter by IP (allow list)
./loganalyzer -ip-allow "10.0.0.1,10.0.0.2" access.log

# Filter by IP (block list)
./loganalyzer -ip-block "192.168.1.100" access.log

# Combine multiple filters
./loganalyzer -status-min 500 -endpoint '/api/' -from 2024-03-01T00:00:00Z logs/

# Disable progress bar
./loganalyzer -no-progress /var/log/
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `-workers` | Number of CPUs | Concurrent worker count |
| `-format` | `table` | Output format: `table`, `json`, `csv` |
| `-from` | (none) | Start date filter (RFC3339) |
| `-to` | (none) | End date filter (RFC3339) |
| `-status-min` | 0 | Minimum status code (inclusive) |
| `-status-max` | 0 | Maximum status code (inclusive) |
| `-endpoint` | (none) | Endpoint path regex filter |
| `-ip-allow` | (none) | Comma-separated IP whitelist |
| `-ip-block` | (none) | Comma-separated IP blacklist |
| `-no-progress` | `false` | Disable progress bar |
| `-version` | | Show version and exit |

## Statistics Reported

- **Summary**: Total requests, unique IPs, unique endpoints, total/skipped lines
- **Status code distribution**: 2xx/3xx/4xx/5xx counts and percentages
- **Top 10 IPs**: By request count
- **Top 10 endpoints**: By request count
- **Top 10 slowest requests**: By response time (if available in logs)
- **Requests per hour**: Histogram with ASCII bar chart
- **Error rate over time**: Hourly error rates with automatic spike detection (>2 standard deviations)

## Supported Log Formats

**Apache Combined / Nginx** (auto-detected):
```
127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /index.html HTTP/1.0" 200 2326 "http://example.com/" "Mozilla/5.0"
```

Optional response time at the end is also parsed:
```
10.0.0.1 - - [15/Mar/2024:08:30:00 +0000] "POST /api HTTP/1.1" 201 512 "-" "curl/7.88" 0.234
```

**JSON Lines** (auto-detected, flexible field names):
```json
{"remote_addr":"10.0.0.1","method":"GET","path":"/api","status":200,"body_bytes_sent":1024,"timestamp":"2024-03-15T10:30:00Z","response_time":0.045}
```

## Project Structure

```
cmd/loganalyzer/       CLI entry point
internal/
  parser/              Log line parsing, format auto-detection
  analyzer/            Statistics computation, merging
  filter/              Date, status, endpoint, IP filtering
  output/              Text table, JSON, CSV formatters, progress bar
  worker/              Concurrent worker pool, file discovery
```

## Testing

```bash
# Run all tests
go test ./... -v

# Run benchmarks
go test ./internal/parser/ -bench=. -benchmem

# Run specific test
go test ./internal/parser/ -run TestParseLine -v
```

## License

MIT
