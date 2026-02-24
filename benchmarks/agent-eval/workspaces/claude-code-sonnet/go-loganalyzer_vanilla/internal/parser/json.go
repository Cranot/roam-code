package parser

import (
	"encoding/json"
	"fmt"
	"time"
)

// JSONParser parses JSON Lines format
type JSONParser struct{}

func NewJSONParser() *JSONParser {
	return &JSONParser{}
}

func (p *JSONParser) Detect(line string) bool {
	var js map[string]interface{}
	return json.Unmarshal([]byte(line), &js) == nil
}

func (p *JSONParser) Parse(line string) (*LogEntry, error) {
	var data map[string]interface{}
	if err := json.Unmarshal([]byte(line), &data); err != nil {
		return nil, fmt.Errorf("failed to parse JSON: %w", err)
	}

	entry := &LogEntry{
		RawLine: line,
	}

	// Try different timestamp field names and formats
	if ts, ok := data["timestamp"].(string); ok {
		timestamp, err := parseTimestamp(ts)
		if err == nil {
			entry.Timestamp = timestamp
		}
	} else if ts, ok := data["time"].(string); ok {
		timestamp, err := parseTimestamp(ts)
		if err == nil {
			entry.Timestamp = timestamp
		}
	} else if ts, ok := data["@timestamp"].(string); ok {
		timestamp, err := parseTimestamp(ts)
		if err == nil {
			entry.Timestamp = timestamp
		}
	}

	// IP
	if ip, ok := data["ip"].(string); ok {
		entry.IP = ip
	} else if ip, ok := data["remote_addr"].(string); ok {
		entry.IP = ip
	} else if ip, ok := data["client_ip"].(string); ok {
		entry.IP = ip
	}

	// Method
	if method, ok := data["method"].(string); ok {
		entry.Method = method
	} else if method, ok := data["request_method"].(string); ok {
		entry.Method = method
	}

	// Endpoint
	if endpoint, ok := data["endpoint"].(string); ok {
		entry.Endpoint = endpoint
	} else if endpoint, ok := data["path"].(string); ok {
		entry.Endpoint = endpoint
	} else if endpoint, ok := data["uri"].(string); ok {
		entry.Endpoint = endpoint
	} else if endpoint, ok := data["request"].(string); ok {
		entry.Endpoint = endpoint
	}

	// Status code
	if status, ok := data["status"].(float64); ok {
		entry.StatusCode = int(status)
	} else if status, ok := data["status_code"].(float64); ok {
		entry.StatusCode = int(status)
	}

	// Response size
	if size, ok := data["size"].(float64); ok {
		entry.ResponseSize = int64(size)
	} else if size, ok := data["bytes_sent"].(float64); ok {
		entry.ResponseSize = int64(size)
	} else if size, ok := data["response_size"].(float64); ok {
		entry.ResponseSize = int64(size)
	}

	// Response time
	if rt, ok := data["response_time"].(float64); ok {
		entry.ResponseTime = rt
	} else if rt, ok := data["duration"].(float64); ok {
		entry.ResponseTime = rt
	} else if rt, ok := data["request_time"].(float64); ok {
		entry.ResponseTime = rt * 1000 // Convert seconds to ms if needed
	}

	// User agent
	if ua, ok := data["user_agent"].(string); ok {
		entry.UserAgent = ua
	}

	// Referrer
	if ref, ok := data["referrer"].(string); ok {
		entry.Referrer = ref
	} else if ref, ok := data["referer"].(string); ok {
		entry.Referrer = ref
	}

	return entry, nil
}

func parseTimestamp(ts string) (time.Time, error) {
	// Try common timestamp formats
	formats := []string{
		time.RFC3339,
		time.RFC3339Nano,
		"2006-01-02T15:04:05.000Z07:00",
		"2006-01-02 15:04:05",
		"2006-01-02T15:04:05",
		"02/Jan/2006:15:04:05 -0700",
	}

	for _, format := range formats {
		if t, err := time.Parse(format, ts); err == nil {
			return t, nil
		}
	}

	return time.Time{}, fmt.Errorf("unable to parse timestamp: %s", ts)
}
