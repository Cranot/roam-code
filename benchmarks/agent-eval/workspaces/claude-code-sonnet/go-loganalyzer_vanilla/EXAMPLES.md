# Log Analyzer Examples

This document provides practical examples of using the Log Analyzer tool.

## Basic Usage

### Analyze a Single File

```bash
./loganalyzer access.log
```

### Analyze Multiple Files

```bash
./loganalyzer access.log error.log nginx.log
```

### Analyze a Directory (Recursive)

```bash
./loganalyzer /var/log/nginx/
```

## Format-Specific Examples

### Apache Combined Logs

```bash
# Auto-detect format
./loganalyzer /var/log/apache2/access.log

# Explicitly specify Apache format
./loganalyzer -log-format apache access.log
```

### Nginx Logs

```bash
# Nginx logs with response time
./loganalyzer -log-format nginx /var/log/nginx/access.log
```

### JSON Lines Logs

```bash
# JSON formatted logs
./loganalyzer -log-format json application.jsonl
```

## Filtering Examples

### Date Range Filtering

```bash
# Last 24 hours
./loganalyzer \
  -start 2023-11-14T00:00:00Z \
  -end 2023-11-15T00:00:00Z \
  access.log

# Specific time window
./loganalyzer \
  -start 2023-11-15T09:00:00Z \
  -end 2023-11-15T17:00:00Z \
  access.log
```

### Status Code Filtering

```bash
# Only errors (4xx and 5xx)
./loganalyzer -min-status 400 access.log

# Only client errors (4xx)
./loganalyzer -min-status 400 -max-status 499 access.log

# Only server errors (5xx)
./loganalyzer -min-status 500 -max-status 599 access.log

# Only successful requests (2xx)
./loganalyzer -min-status 200 -max-status 299 access.log
```

### Endpoint Filtering

```bash
# API endpoints only
./loganalyzer -endpoint "api" access.log

# Specific API version
./loganalyzer -endpoint "^/api/v2/" access.log

# Static assets
./loganalyzer -endpoint "\.(js|css|png|jpg|gif)$" access.log

# Exclude static assets
./loganalyzer -endpoint "^/(?!static)" access.log
```

### IP Filtering

```bash
# Whitelist specific IPs
./loganalyzer -ip-whitelist "192.168.1.100,192.168.1.101" access.log

# Blacklist specific IPs (e.g., known bots)
./loganalyzer -ip-blacklist "10.0.0.1,10.0.0.2" access.log
```

### Combined Filters

```bash
# API errors from yesterday
./loganalyzer \
  -endpoint "^/api/" \
  -min-status 400 \
  -start 2023-11-14T00:00:00Z \
  -end 2023-11-15T00:00:00Z \
  access.log

# Slow API requests with errors
./loganalyzer \
  -endpoint "^/api/" \
  -min-status 500 \
  -format json \
  access.log
```

## Output Format Examples

### Text Table (Default)

```bash
./loganalyzer access.log
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
...
```

### JSON Output

```bash
./loganalyzer -format json access.log > report.json
```

```bash
# Pretty-printed JSON
./loganalyzer -format json access.log | jq '.'
```

### CSV Output

```bash
./loganalyzer -format csv access.log > report.csv
```

```bash
# Import into Excel or spreadsheet
./loganalyzer -format csv /var/log/nginx/ > weekly_report.csv
```

## Performance Examples

### High Concurrency Processing

```bash
# Use 16 workers for large log directories
./loganalyzer -workers 16 /var/log/nginx/

# Use all available CPU cores
./loganalyzer -workers $(nproc) /var/log/
```

### Disable Progress Bar

```bash
# For scripting or piping
./loganalyzer -no-progress access.log > report.txt

# For automation
./loganalyzer -no-progress -format json access.log | jq '.summary.unique_ips'
```

## Real-World Scenarios

### Security Analysis: Find Suspicious Activity

```bash
# Find failed authentication attempts
./loganalyzer \
  -endpoint "/login" \
  -min-status 401 \
  -max-status 403 \
  -format json \
  access.log
```

### Performance Analysis: Identify Slow Endpoints

```bash
# Analyze API performance with response times
./loganalyzer \
  -endpoint "^/api/" \
  -format json \
  nginx.log | jq '.slow_requests'
```

### Traffic Analysis: Peak Hours

```bash
# Get hourly traffic distribution
./loganalyzer -format json access.log | jq '.hourly_histogram'
```

### Error Monitoring: Spike Detection

```bash
# Detect error rate spikes
./loganalyzer -format json access.log | jq '.error_spikes'
```

### API Endpoint Usage

```bash
# Top API endpoints by request count
./loganalyzer \
  -endpoint "^/api/" \
  -format json \
  access.log | jq '.top_endpoints'
```

### Geographic Analysis: Top IPs

```bash
# Export top IPs for geographic lookup
./loganalyzer -format csv access.log | grep "Top IPs" -A 100 > top_ips.csv
```

## Automation Examples

### Daily Report Generation

```bash
#!/bin/bash
# daily_report.sh

DATE=$(date -d "yesterday" +%Y-%m-%d)
LOGS="/var/log/nginx/access.log"

./loganalyzer \
  -start "${DATE}T00:00:00Z" \
  -end "${DATE}T23:59:59Z" \
  -format json \
  "$LOGS" > "reports/daily_${DATE}.json"

echo "Daily report generated: reports/daily_${DATE}.json"
```

### Error Alert Script

```bash
#!/bin/bash
# check_errors.sh

ERROR_COUNT=$(./loganalyzer \
  -min-status 500 \
  -format json \
  -no-progress \
  /var/log/nginx/access.log | jq '.summary.valid_requests')

if [ "$ERROR_COUNT" -gt 100 ]; then
  echo "ALERT: High error count detected: $ERROR_COUNT 5xx errors"
  # Send alert (email, Slack, PagerDuty, etc.)
fi
```

### API Monitoring

```bash
#!/bin/bash
# monitor_api.sh

./loganalyzer \
  -endpoint "^/api/" \
  -format json \
  -no-progress \
  access.log > api_metrics.json

# Extract metrics
TOTAL=$(jq '.summary.valid_requests' api_metrics.json)
ERROR_RATE=$(jq '.status_distribution."5xx".percentage' api_metrics.json)

echo "API Requests: $TOTAL"
echo "Error Rate: $ERROR_RATE%"
```

### Log Rotation Analysis

```bash
#!/bin/bash
# analyze_rotated_logs.sh

# Analyze all rotated logs from the past week
./loganalyzer \
  -workers 8 \
  -format csv \
  /var/log/nginx/access.log* > weekly_summary.csv

echo "Weekly summary generated: weekly_summary.csv"
```

## Integration Examples

### Pipeline with jq

```bash
# Get unique IPs count
./loganalyzer -format json access.log | jq '.summary.unique_ips'

# Get error percentage
./loganalyzer -format json access.log | jq '.status_distribution."5xx".percentage'

# List all error spikes
./loganalyzer -format json access.log | jq '.error_spikes[]'
```

### Pipeline with awk

```bash
# Extract top 5 IPs
./loganalyzer -format csv access.log | grep "^[0-9]" | head -5
```

### Combine with Other Tools

```bash
# Analyze logs and create visualization
./loganalyzer -format json access.log > data.json
python visualize.py data.json

# Send to monitoring system
./loganalyzer -format json access.log | curl -X POST -d @- http://monitoring.example.com/api/logs
```

## Tips and Best Practices

1. **Use format-specific flags when format is known** to skip auto-detection and improve performance
2. **Use -no-progress for scripts** to avoid terminal escape codes in logs
3. **Filter at analysis time** rather than preprocessing logs to reduce I/O
4. **Use JSON output for programmatic access** and piping to other tools
5. **Increase workers for large datasets** to maximize throughput
6. **Combine filters** to narrow down specific issues quickly
7. **Use regex endpoint filtering** for flexible pattern matching
8. **Export to CSV** for spreadsheet analysis and reporting
