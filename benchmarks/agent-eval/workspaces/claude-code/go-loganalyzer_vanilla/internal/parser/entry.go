package parser

import "time"

// LogEntry represents a single parsed log line.
type LogEntry struct {
	RemoteAddr string
	Method     string
	Path       string
	Protocol   string
	StatusCode int
	BodyBytes  int64
	Referer    string
	UserAgent  string
	Timestamp  time.Time
	// ResponseTime in seconds (may be zero if not available).
	ResponseTime float64
	SourceFile   string
	LineNumber   int
}

// Format represents a log format type.
type Format int

const (
	FormatUnknown Format = iota
	FormatApacheCombined
	FormatNginx
	FormatJSONLines
)

func (f Format) String() string {
	switch f {
	case FormatApacheCombined:
		return "apache_combined"
	case FormatNginx:
		return "nginx"
	case FormatJSONLines:
		return "json_lines"
	default:
		return "unknown"
	}
}
