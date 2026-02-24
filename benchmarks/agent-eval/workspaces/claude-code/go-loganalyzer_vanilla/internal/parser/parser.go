package parser

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Result holds the outcome of parsing a log file.
type Result struct {
	Entries      []LogEntry
	SkippedLines int
	TotalLines   int
	Format       Format
}

// apacheCombinedRe matches Apache Combined Log Format:
// 127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326 "http://www.example.com/start.html" "Mozilla/4.08"
var apacheCombinedRe = regexp.MustCompile(
	`^(\S+)\s+\S+\s+\S+\s+\[([^\]]+)\]\s+"(\S+)\s+(\S+)\s*(\S*)"\s+(\d{3})\s+(\d+|-)\s+"([^"]*)"\s+"([^"]*)"(?:\s+(\S+))?`,
)

// nginxRe matches a common Nginx log format (same as Apache Combined but may include request_time at end).
var nginxRe = apacheCombinedRe

// ParseLine attempts to parse a single log line, auto-detecting the format.
func ParseLine(line string, lineNum int, sourceFile string) (LogEntry, Format, error) {
	line = strings.TrimSpace(line)
	if line == "" {
		return LogEntry{}, FormatUnknown, fmt.Errorf("empty line")
	}

	// Try JSON first (starts with '{').
	if line[0] == '{' {
		entry, err := parseJSONLine(line, lineNum, sourceFile)
		if err == nil {
			return entry, FormatJSONLines, nil
		}
	}

	// Try Apache/Nginx combined format.
	entry, err := parseApacheCombined(line, lineNum, sourceFile)
	if err == nil {
		return entry, FormatApacheCombined, nil
	}

	return LogEntry{}, FormatUnknown, fmt.Errorf("unrecognized format")
}

func parseApacheCombined(line string, lineNum int, sourceFile string) (LogEntry, error) {
	matches := apacheCombinedRe.FindStringSubmatch(line)
	if matches == nil {
		return LogEntry{}, fmt.Errorf("does not match apache combined format")
	}

	ts, err := time.Parse("02/Jan/2006:15:04:05 -0700", matches[2])
	if err != nil {
		return LogEntry{}, fmt.Errorf("bad timestamp: %w", err)
	}

	status, _ := strconv.Atoi(matches[6])
	bodyBytes := int64(0)
	if matches[7] != "-" {
		bodyBytes, _ = strconv.ParseInt(matches[7], 10, 64)
	}

	entry := LogEntry{
		RemoteAddr: matches[1],
		Method:     matches[3],
		Path:       matches[4],
		Protocol:   matches[5],
		StatusCode: status,
		BodyBytes:  bodyBytes,
		Referer:    matches[8],
		UserAgent:  matches[9],
		Timestamp:  ts,
		SourceFile: sourceFile,
		LineNumber: lineNum,
	}

	// Optional response time at the end.
	if len(matches) > 10 && matches[10] != "" {
		if rt, err := strconv.ParseFloat(matches[10], 64); err == nil {
			entry.ResponseTime = rt
		}
	}

	return entry, nil
}

// jsonLogEntry is the structure for JSON Lines log parsing.
type jsonLogEntry struct {
	RemoteAddr   string  `json:"remote_addr"`
	IP           string  `json:"ip"`
	ClientIP     string  `json:"client_ip"`
	Method       string  `json:"method"`
	Request      string  `json:"request"`
	Path         string  `json:"path"`
	URI          string  `json:"uri"`
	URL          string  `json:"url"`
	Protocol     string  `json:"protocol"`
	Status       int     `json:"status"`
	StatusCode   int     `json:"status_code"`
	BodyBytes    int64   `json:"body_bytes_sent"`
	Size         int64   `json:"size"`
	Referer      string  `json:"referer"`
	HTTPReferer  string  `json:"http_referer"`
	UserAgent    string  `json:"user_agent"`
	HTTPUserAgent string `json:"http_user_agent"`
	Timestamp    string  `json:"timestamp"`
	Time         string  `json:"time"`
	TimeLocal    string  `json:"time_local"`
	ResponseTime float64 `json:"response_time"`
	RequestTime  float64 `json:"request_time"`
	Upstream     float64 `json:"upstream_response_time"`
}

func parseJSONLine(line string, lineNum int, sourceFile string) (LogEntry, error) {
	var j jsonLogEntry
	if err := json.Unmarshal([]byte(line), &j); err != nil {
		return LogEntry{}, err
	}

	entry := LogEntry{
		SourceFile: sourceFile,
		LineNumber: lineNum,
	}

	// Remote address: try multiple field names.
	entry.RemoteAddr = coalesceStr(j.RemoteAddr, j.IP, j.ClientIP)

	// Method and path.
	if j.Request != "" {
		parts := strings.Fields(j.Request)
		if len(parts) >= 2 {
			entry.Method = parts[0]
			entry.Path = parts[1]
			if len(parts) >= 3 {
				entry.Protocol = parts[2]
			}
		}
	} else {
		entry.Method = j.Method
		entry.Path = coalesceStr(j.Path, j.URI, j.URL)
		entry.Protocol = j.Protocol
	}

	// Status.
	if j.Status != 0 {
		entry.StatusCode = j.Status
	} else {
		entry.StatusCode = j.StatusCode
	}

	// Body bytes.
	if j.BodyBytes != 0 {
		entry.BodyBytes = j.BodyBytes
	} else {
		entry.BodyBytes = j.Size
	}

	// Referer.
	entry.Referer = coalesceStr(j.Referer, j.HTTPReferer)

	// User agent.
	entry.UserAgent = coalesceStr(j.UserAgent, j.HTTPUserAgent)

	// Response time.
	if j.ResponseTime != 0 {
		entry.ResponseTime = j.ResponseTime
	} else if j.RequestTime != 0 {
		entry.ResponseTime = j.RequestTime
	} else if j.Upstream != 0 {
		entry.ResponseTime = j.Upstream
	}

	// Timestamp.
	tsStr := coalesceStr(j.Timestamp, j.Time, j.TimeLocal)
	if tsStr != "" {
		entry.Timestamp = parseTimestamp(tsStr)
	}

	if entry.RemoteAddr == "" && entry.Path == "" && entry.StatusCode == 0 {
		return LogEntry{}, fmt.Errorf("json line has no useful fields")
	}

	return entry, nil
}

func coalesceStr(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

var tsFormats = []string{
	time.RFC3339,
	time.RFC3339Nano,
	"2006-01-02T15:04:05",
	"2006-01-02 15:04:05",
	"02/Jan/2006:15:04:05 -0700",
	"02/Jan/2006:15:04:05",
}

func parseTimestamp(s string) time.Time {
	for _, layout := range tsFormats {
		if t, err := time.Parse(layout, s); err == nil {
			return t
		}
	}
	return time.Time{}
}

// ParseReader reads and parses all lines from a reader.
func ParseReader(r io.Reader, sourceFile string) Result {
	var result Result
	scanner := bufio.NewScanner(r)
	// Support lines up to 1MB.
	buf := make([]byte, 0, 64*1024)
	scanner.Buffer(buf, 1024*1024)

	lineNum := 0
	formatCounts := make(map[Format]int)

	for scanner.Scan() {
		lineNum++
		result.TotalLines = lineNum
		line := scanner.Text()
		if strings.TrimSpace(line) == "" {
			result.SkippedLines++
			continue
		}

		entry, format, err := ParseLine(line, lineNum, sourceFile)
		if err != nil {
			result.SkippedLines++
			continue
		}
		result.Entries = append(result.Entries, entry)
		formatCounts[format]++
	}

	// Determine dominant format.
	maxCount := 0
	for f, c := range formatCounts {
		if c > maxCount {
			maxCount = c
			result.Format = f
		}
	}

	return result
}
