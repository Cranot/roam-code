package parser

import (
	"fmt"
	"regexp"
	"strconv"
	"time"
)

// ApacheParser parses Apache Combined Log Format
// Format: IP - - [timestamp] "METHOD endpoint HTTP/version" status size "referrer" "user-agent"
type ApacheParser struct {
	regex *regexp.Regexp
}

func NewApacheParser() *ApacheParser {
	// Apache Combined Log Format regex
	pattern := `^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) ([^"]+) HTTP/[^"]+" (\d+) (\d+|-) "([^"]*)" "([^"]*)"`
	return &ApacheParser{
		regex: regexp.MustCompile(pattern),
	}
}

func (p *ApacheParser) Detect(line string) bool {
	return p.regex.MatchString(line)
}

func (p *ApacheParser) Parse(line string) (*LogEntry, error) {
	matches := p.regex.FindStringSubmatch(line)
	if matches == nil {
		return nil, fmt.Errorf("line does not match Apache format")
	}

	entry := &LogEntry{
		RawLine: line,
	}

	// IP
	entry.IP = matches[1]

	// Timestamp
	// Apache format: 10/Oct/2000:13:55:36 -0700
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

	return entry, nil
}
