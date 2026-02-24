package parser

import "time"

// LogEntry represents a parsed log line
type LogEntry struct {
	Timestamp    time.Time
	IP           string
	Method       string
	Endpoint     string
	StatusCode   int
	ResponseSize int64
	ResponseTime float64 // in milliseconds
	UserAgent    string
	Referrer     string
	RawLine      string
}

// LogFormat represents the detected log format
type LogFormat int

const (
	FormatUnknown LogFormat = iota
	FormatApacheCombined
	FormatNginx
	FormatJSONLines
)

func (f LogFormat) String() string {
	switch f {
	case FormatApacheCombined:
		return "Apache Combined"
	case FormatNginx:
		return "Nginx"
	case FormatJSONLines:
		return "JSON Lines"
	default:
		return "Unknown"
	}
}

// Parser interface for different log formats
type Parser interface {
	Parse(line string) (*LogEntry, error)
	Detect(line string) bool
}
