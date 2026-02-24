package parser

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"go-loganalyzer/internal/model"
)

type Format string

const (
	FormatUnknown Format = "unknown"
	FormatApache  Format = "apache_combined"
	FormatNginx   Format = "nginx"
	FormatJSONL   Format = "jsonl"
)

var accessLogPrefixRe = regexp.MustCompile(`^(\S+) \S+ \S+ \[([^\]]+)\] "([^"]*)" (\d{3})`)
var responseTimePatterns = []*regexp.Regexp{
	regexp.MustCompile(`request_time(?:=|:|\s)(\d+(?:\.\d+)?)`),
	regexp.MustCompile(`rt=(\d+(?:\.\d+)?)`),
	regexp.MustCompile(`\s(\d+\.\d+)\s*$`),
}

type LineParser interface {
	ParseLine(line string) (model.Entry, error)
}

type accessParser struct{}
type jsonLineParser struct{}

func DetectFormat(lines []string) Format {
	var jsonScore int
	var accessScore int
	var nginxScore int

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		if looksLikeJSON(line) {
			var raw map[string]any
			if err := json.Unmarshal([]byte(line), &raw); err == nil {
				jsonScore++
			}
		}
		if accessLogPrefixRe.MatchString(line) {
			accessScore++
			if looksLikeNginx(line) {
				nginxScore++
			}
		}
	}

	if jsonScore > 0 && jsonScore >= accessScore {
		return FormatJSONL
	}
	if accessScore > 0 {
		if nginxScore > 0 {
			return FormatNginx
		}
		return FormatApache
	}
	return FormatUnknown
}

func NewLineParser(format Format) LineParser {
	switch format {
	case FormatJSONL:
		return jsonLineParser{}
	case FormatApache, FormatNginx:
		return accessParser{}
	default:
		return autoLineParser{}
	}
}

type autoLineParser struct{}

func (a autoLineParser) ParseLine(line string) (model.Entry, error) {
	line = strings.TrimSpace(line)
	if line == "" {
		return model.Entry{}, errors.New("empty line")
	}
	if looksLikeJSON(line) {
		entry, err := jsonLineParser{}.ParseLine(line)
		if err == nil {
			return entry, nil
		}
	}
	if accessLogPrefixRe.MatchString(line) {
		return accessParser{}.ParseLine(line)
	}
	return model.Entry{}, fmt.Errorf("unsupported line format")
}

func (p accessParser) ParseLine(line string) (model.Entry, error) {
	matches := accessLogPrefixRe.FindStringSubmatch(line)
	if len(matches) != 5 {
		return model.Entry{}, errors.New("invalid access log line")
	}

	ip := matches[1]
	timestamp, err := parseAccessTimestamp(matches[2])
	if err != nil {
		return model.Entry{}, fmt.Errorf("invalid timestamp: %w", err)
	}
	endpoint := parseEndpointFromRequest(matches[3])
	status, err := strconv.Atoi(matches[4])
	if err != nil {
		return model.Entry{}, fmt.Errorf("invalid status code: %w", err)
	}

	entry := model.Entry{
		Timestamp: timestamp,
		IP:        ip,
		Endpoint:  endpoint,
		Status:    status,
	}

	if responseTimeMs, ok := extractResponseTimeMs(line); ok {
		entry.ResponseTimeMs = responseTimeMs
		entry.HasResponseTime = true
	}

	return entry, nil
}

func (p jsonLineParser) ParseLine(line string) (model.Entry, error) {
	var raw map[string]any
	if err := json.Unmarshal([]byte(line), &raw); err != nil {
		return model.Entry{}, fmt.Errorf("invalid json line: %w", err)
	}

	ip := firstString(raw, []string{"ip", "remote_addr", "client_ip", "remote_ip"})
	if ip == "" {
		return model.Entry{}, errors.New("missing ip field")
	}

	status, ok := firstInt(raw, []string{"status", "status_code", "code"})
	if !ok {
		return model.Entry{}, errors.New("missing status field")
	}

	timestamp, ok := firstTimestamp(raw, []string{"timestamp", "@timestamp", "time", "ts", "date"})
	if !ok {
		return model.Entry{}, errors.New("missing timestamp field")
	}

	endpoint := firstString(raw, []string{"endpoint", "path", "uri", "url"})
	if endpoint == "" {
		request := firstString(raw, []string{"request", "http_request"})
		if request != "" {
			endpoint = parseEndpointFromRequest(request)
		}
	}
	if endpoint == "" {
		endpoint = "-"
	}

	entry := model.Entry{
		Timestamp: timestamp,
		IP:        ip,
		Endpoint:  endpoint,
		Status:    status,
	}

	if v, key, ok := firstNumberWithKey(raw, []string{
		"response_time_ms",
		"duration_ms",
		"latency_ms",
		"response_time",
		"request_time",
		"duration",
		"latency",
	}); ok {
		entry.ResponseTimeMs = normalizeDurationToMs(v, key)
		entry.HasResponseTime = true
	}

	return entry, nil
}

func parseAccessTimestamp(value string) (time.Time, error) {
	return time.Parse("02/Jan/2006:15:04:05 -0700", value)
}

func parseEndpointFromRequest(request string) string {
	parts := strings.Fields(request)
	if len(parts) >= 2 {
		return parts[1]
	}
	return "-"
}

func extractResponseTimeMs(line string) (float64, bool) {
	for _, pattern := range responseTimePatterns {
		matches := pattern.FindStringSubmatch(line)
		if len(matches) != 2 {
			continue
		}
		v, err := strconv.ParseFloat(matches[1], 64)
		if err != nil {
			continue
		}
		return normalizeDurationToMs(v, "request_time"), true
	}
	return 0, false
}

func normalizeDurationToMs(value float64, key string) float64 {
	k := strings.ToLower(key)
	if strings.Contains(k, "ms") {
		return value
	}
	if value >= 1000 {
		return value
	}
	return value * 1000
}

func looksLikeJSON(line string) bool {
	return strings.HasPrefix(line, "{") && strings.HasSuffix(line, "}")
}

func looksLikeNginx(line string) bool {
	lowered := strings.ToLower(line)
	if strings.Contains(lowered, "request_time") || strings.Contains(lowered, "upstream_response_time") || strings.Contains(lowered, " rt=") {
		return true
	}
	return responseTimePatterns[2].MatchString(line)
}

func firstString(raw map[string]any, keys []string) string {
	for _, key := range keys {
		if value, exists := raw[key]; exists {
			switch v := value.(type) {
			case string:
				return strings.TrimSpace(v)
			}
		}
	}
	return ""
}

func firstInt(raw map[string]any, keys []string) (int, bool) {
	for _, key := range keys {
		if value, exists := raw[key]; exists {
			switch v := value.(type) {
			case float64:
				return int(v), true
			case int:
				return v, true
			case int64:
				return int(v), true
			case string:
				parsed, err := strconv.Atoi(v)
				if err == nil {
					return parsed, true
				}
			}
		}
	}
	return 0, false
}

func firstTimestamp(raw map[string]any, keys []string) (time.Time, bool) {
	for _, key := range keys {
		value, exists := raw[key]
		if !exists {
			continue
		}
		if ts, ok := parseTimestamp(value); ok {
			return ts, true
		}
	}
	return time.Time{}, false
}

func parseTimestamp(value any) (time.Time, bool) {
	switch v := value.(type) {
	case string:
		layouts := []string{
			time.RFC3339Nano,
			time.RFC3339,
			"2006-01-02 15:04:05",
			"2006-01-02T15:04:05",
			"02/Jan/2006:15:04:05 -0700",
			time.UnixDate,
			time.RubyDate,
		}
		for _, layout := range layouts {
			if ts, err := time.Parse(layout, v); err == nil {
				return ts, true
			}
		}
	case float64:
		return parseUnixTimestamp(v)
	case int64:
		return parseUnixTimestamp(float64(v))
	case int:
		return parseUnixTimestamp(float64(v))
	}
	return time.Time{}, false
}

func parseUnixTimestamp(v float64) (time.Time, bool) {
	switch {
	case v > 1e18:
		return time.Unix(0, int64(v)), true
	case v > 1e12:
		return time.UnixMilli(int64(v)), true
	default:
		return time.Unix(int64(v), 0), true
	}
}

func firstNumberWithKey(raw map[string]any, keys []string) (float64, string, bool) {
	for _, key := range keys {
		value, exists := raw[key]
		if !exists {
			continue
		}
		switch v := value.(type) {
		case float64:
			return v, key, true
		case int:
			return float64(v), key, true
		case int64:
			return float64(v), key, true
		case string:
			parsed, err := strconv.ParseFloat(v, 64)
			if err == nil {
				return parsed, key, true
			}
		}
	}
	return 0, "", false
}
