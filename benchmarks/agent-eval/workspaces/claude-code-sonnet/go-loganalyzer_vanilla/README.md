# Log Analyzer

A high-performance concurrent log file analyzer CLI tool written in Go. Supports Apache Combined, Nginx, and JSON Lines log formats with automatic format detection.

## Features

- **Multiple Log Formats**: Apache Combined, Nginx, JSON Lines with auto-detection
- **Concurrent Processing**: Worker pool with configurable goroutines for parallel file processing
- **Comprehensive Statistics**:
  - Total requests, unique IPs, unique endpoints
  - Status code distribution (2xx, 3xx, 4xx, 5xx with counts and percentages)
  - Top 10 IPs by request count
  - Top 10 endpoints by request count
  - Top 10 slowest requests (when response time is available)
  - Hourly request histogram
  - Error rate spike detection
- **Flexible Filtering**:
  - Date range (start/end timestamps)
  - Status code range
  - Endpoint regex matching
  - IP whitelist/blacklist
- **Multiple Output Formats**: Text tables (default), JSON, CSV
- **Progress Indicator**: Real-time progress bar for large file processing
- **Robust Error Handling**: Gracefully handles malformed lines with reporting

## Installation

### Build from Source

```bash
# Clone or download the source
git clone <repository-url>
cd loganalyzer

# Build the binary
go build -o loganalyzer cmd/loganalyzer/main.go

# Optional: Install to $GOPATH/bin
go install ./cmd/loganalyzer
```

### Run Tests

```bash
# Run all tests
go test ./...

# Run tests with verbose output
go test -v ./...

# Run tests with coverage
go test -cover ./...

# Run benchmarks
go test -bench=. ./internal/parser/
```

## Usage

### Basic Usage

```bash
# Analyze a single log file
./loganalyzer access.log

# Analyze multiple files
./loganalyzer access.log error.log

# Analyze all logs in a directory (recursive)
./loganalyzer /var/log/nginx/
```

### Format Options

```bash
# Auto-detect format (default)
./loganalyzer access.log

# Specify format explicitly
./loganalyzer -log-format apache access.log
./loganalyzer -log-format nginx access.log
./loganalyzer -log-format json logs.jsonl
```

### Output Formats

```bash
# Text table output (default)
./loganalyzer access.log

# JSON output
./loganalyzer -format json access.log

# CSV output
./loganalyzer -format csv access.log > report.csv
```

### Filtering

```bash
# Filter by date range (RFC3339 format)
./loganalyzer -start 2023-11-15T00:00:00Z -end 2023-11-16T00:00:00Z access.log

# Filter by status code range (e.g., only errors)
./loganalyzer -min-status 400 -max-status 599 access.log

# Filter by endpoint regex
./loganalyzer -endpoint "^/api/" access.log

# IP whitelist (only these IPs)
./loganalyzer -ip-whitelist "192.168.1.1,192.168.1.2" access.log

# IP blacklist (exclude these IPs)
./loganalyzer -ip-blacklist "10.0.0.1,10.0.0.2" access.log

# Combine filters
./loganalyzer -min-status 400 -endpoint "^/api/" -format json access.log
```

### Performance Options

```bash
# Set number of concurrent workers (default: number of CPUs)
./loganalyzer -workers 8 access.log

# Disable progress bar (useful for scripting)
./loganalyzer -no-progress access.log
```

## Examples

### Example 1: Analyze Nginx Access Logs

```bash
./loganalyzer /var/log/nginx/access.log
```

Output:
```
=== Log Analysis Results ===

Total Lines Processed: 10000
Valid Requests:        9876
Skipped Lines:         124
Unique IPs:            542
Unique Endpoints:      87

=== Status Code Distribution ===
2xx (Success):       8234 (83.37%)
3xx (Redirect):       856 (8.67%)
4xx (Client Error):   623 (6.31%)
5xx (Server Error):   163 (1.65%)

=== Top 10 IPs by Request Count ===
IP Address      Requests
---------------------------
192.168.1.100       234
10.0.0.45           198
...
```

### Example 2: Find API Errors in JSON Logs

```bash
./loganalyzer -endpoint "^/api/" -min-status 400 -format json logs.jsonl
```

### Example 3: Generate CSV Report for Last Week

```bash
./loganalyzer \
  -start 2023-11-08T00:00:00Z \
  -end 2023-11-15T00:00:00Z \
  -format csv \
  /var/log/apache2/access.log > weekly_report.csv
```

### Example 4: Analyze Multiple Directories with High Concurrency

```bash
./loganalyzer -workers 16 /var/log/nginx/ /var/log/apache2/
```

## Log Format Support

### Apache Combined Log Format

```
127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /index.html HTTP/1.0" 200 2326 "http://example.com" "Mozilla/5.0"
```

### Nginx Log Format

```
10.0.0.1 - - [15/Nov/2023:12:00:00 +0000] "GET /api/users HTTP/1.1" 200 512 "-" "Mozilla/5.0" 0.123
```

Note: Response time (last field) is optional.

### JSON Lines Format

```json
{"timestamp":"2023-11-15T12:00:00Z","ip":"192.168.1.100","method":"GET","path":"/api/test","status":200,"size":1024}
```

Supported JSON field names (flexible):
- **Timestamp**: `timestamp`, `time`, `@timestamp`
- **IP**: `ip`, `remote_addr`, `client_ip`
- **Method**: `method`, `request_method`
- **Endpoint**: `endpoint`, `path`, `uri`, `request`
- **Status**: `status`, `status_code`
- **Size**: `size`, `bytes_sent`, `response_size`
- **Response Time**: `response_time`, `duration`, `request_time`
- **User Agent**: `user_agent`
- **Referrer**: `referrer`, `referer`

## Architecture

```
loganalyzer/
├── cmd/
│   └── loganalyzer/
│       └── main.go           # CLI entry point
├── internal/
│   ├── analyzer/
│   │   ├── filters.go        # Request filtering
│   │   └── statistics.go     # Statistics aggregation
│   ├── output/
│   │   ├── csv.go           # CSV formatter
│   │   ├── formatter.go      # Formatter interface
│   │   ├── json.go          # JSON formatter
│   │   ├── progress.go      # Progress bar
│   │   └── text.go          # Text table formatter
│   ├── parser/
│   │   ├── apache.go        # Apache parser
│   │   ├── autodetect.go    # Format auto-detection
│   │   ├── json.go          # JSON parser
│   │   ├── nginx.go         # Nginx parser
│   │   └── types.go         # Core types
│   ├── scanner/
│   │   └── scanner.go       # File discovery
│   └── worker/
│       └── pool.go          # Worker pool
└── go.mod
```

## Performance

- **Concurrent Processing**: Uses worker pools to process multiple files in parallel
- **Efficient Parsing**: Regex-based parsing with minimal allocations
- **Streaming**: Processes files line-by-line without loading into memory
- **Benchmarks**: Run `go test -bench=. ./internal/parser/` to see parser performance

Example benchmark results:
```
BenchmarkApacheParser-8    500000    2500 ns/op
BenchmarkNginxParser-8     450000    2600 ns/op
BenchmarkJSONParser-8      300000    3800 ns/op
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Run tests: `go test ./...`
6. Run benchmarks: `go test -bench=. ./internal/parser/`
7. Submit a pull request

## Testing

The project uses table-driven tests following Go best practices:

```bash
# Run all tests
go test ./...

# Run specific package tests
go test ./internal/parser/

# Run with coverage
go test -cover ./...

# Run with race detector
go test -race ./...

# Run benchmarks
go test -bench=. -benchmem ./internal/parser/
```

## License

MIT License - see LICENSE file for details
