# Quick Start Guide

Get started with Log Analyzer in 5 minutes!

## Installation

### Option 1: Build from Source

```bash
# Clone the repository
cd loganalyzer

# Build
go build -o loganalyzer cmd/loganalyzer/main.go

# Verify installation
./loganalyzer -h
```

### Option 2: Using Make

```bash
make build
./loganalyzer -h
```

## Basic Usage

### 1. Analyze a Single File

```bash
./loganalyzer access.log
```

You'll see output like:
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
...
```

### 2. Analyze a Directory

```bash
./loganalyzer /var/log/nginx/
```

The tool will recursively find and analyze all log files in the directory.

### 3. Get JSON Output

```bash
./loganalyzer -format json access.log
```

Perfect for piping to other tools or saving to a file:
```bash
./loganalyzer -format json access.log > report.json
```

## Common Use Cases

### Find All Errors

```bash
./loganalyzer -min-status 400 access.log
```

### Analyze API Endpoints Only

```bash
./loganalyzer -endpoint "api" access.log
```

### Generate CSV Report

```bash
./loganalyzer -format csv access.log > report.csv
```

### Analyze Specific Time Range

```bash
./loganalyzer \
  -start 2023-11-15T00:00:00Z \
  -end 2023-11-16T00:00:00Z \
  access.log
```

### High-Performance Processing

```bash
# Use more workers for large datasets
./loganalyzer -workers 16 /var/log/
```

## Testing the Tool

The repository includes sample log files for testing:

```bash
# Test with Apache logs
./loganalyzer test_logs/apache_sample.log

# Test with Nginx logs
./loganalyzer test_logs/nginx_sample.log

# Test with JSON logs
./loganalyzer test_logs/json_sample.jsonl

# Test with all samples
./loganalyzer test_logs/
```

## Development

### Run Tests

```bash
# All tests
go test ./...

# With coverage
go test -cover ./...

# Verbose
go test -v ./...
```

### Run Benchmarks

```bash
go test -bench=. ./internal/parser/
```

### Format Code

```bash
go fmt ./...
```

## Help

View all available options:

```bash
./loganalyzer -h
```

For more examples, see:
- [README.md](README.md) - Complete documentation
- [EXAMPLES.md](EXAMPLES.md) - Real-world examples
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - Technical overview

## Troubleshooting

### "No log files found"
- Ensure the path is correct
- Check that files have appropriate extensions (.log, .txt, .json)
- Try specifying files directly instead of directories

### "Error parsing logs"
- Check the log format with `-log-format` flag
- Verify the log file is not corrupted
- Look at the "Skipped Lines" count in the output

### Performance issues
- Increase workers: `-workers 16`
- Disable progress bar: `-no-progress`
- Process smaller batches of files

## Next Steps

1. Read [EXAMPLES.md](EXAMPLES.md) for advanced filtering and use cases
2. Check [README.md](README.md) for complete flag reference
3. Explore automation with shell scripts
4. Integrate with your monitoring pipeline

Happy log analyzing! 🔍
