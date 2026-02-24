package parser

import (
	"fmt"
	"regexp"
	"strconv"
	"time"
)

// NginxParser parses Nginx log format
// Format: IP - - [timestamp] "METHOD endpoint HTTP/version" status size "referrer" "user-agent" response_time
type NginxParser struct {
	regex *regexp.Regexp
}

func NewNginxParser() *NginxParser {
	// Nginx format with optional response time at the end
	pattern := `^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) ([^"]+) HTTP/[^"]+" (\d+) (\d+|-) "([^"]*)" "([^"]*)"(?:\s+([\d.]+))?`
	return &NginxParser{
		regex: regexp.MustCompile(pattern),
	}
}

func (p *NginxParser) Detect(line string) bool {
	return p.regex.MatchString(line)
}

func (p *NginxParser) Parse(line string) (*LogEntry, error) {
	matches := p.regex.FindStringSubmatch(line)
	if matches == nil {
		return nil, fmt.Errorf("line does not match Nginx format")
	}

	entry := &LogEntry{
		RawLine: line,
	}

	// IP
	entry.IP = matches[1]

	// Timestamp
	timestamp, err := time.Parse("02/Jan/2006:15:04:05 -0700", matches[2])
	if err != nil {
		return nil, fmt.Errorf("failed to parse timestamp: %w", err)
	}
	entry.Timestamp = timestamp

	// Method
	entry.Method = matches[3]

	// Endpoint
	entry.Endpoint = matches[4]

	// Status code
	status, err := strconv.Atoi(matches[5])
	if err != nil {
		return nil, fmt.Errorf("failed to parse status code: %w", err)
	}
	entry.StatusCode = status

	// Response size
	if matches[6] != "-" {
		size, err := strconv.ParseInt(matches[6], 10, 64)
		if err != nil {
			return nil, fmt.Errorf("failed to parse response size: %w", err)
		}
		entry.ResponseSize = size
	}

	// Referrer
	entry.Referrer = matches[7]

	// User agent
	entry.UserAgent = matches[8]

	// Response time (optional)
	if len(matches) > 9 && matches[9] != "" {
		responseTime, err := strconv.ParseFloat(matches[9], 64)
		if err == nil {
			entry.ResponseTime = responseTime * 1000 // Convert seconds to milliseconds
		}
	}

	return entry, nil
}
