package filter

import (
	"regexp"
	"testing"
	"time"

	"github.com/loganalyzer/internal/parser"
)

func makeEntry(ip, path string, status int, ts time.Time) parser.LogEntry {
	return parser.LogEntry{
		RemoteAddr: ip,
		Method:     "GET",
		Path:       path,
		StatusCode: status,
		Timestamp:  ts,
	}
}

func TestApplyNoop(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "/a", 200, time.Now()),
		makeEntry("2.2.2.2", "/b", 404, time.Now()),
	}
	result := Apply(entries, Options{})
	if len(result) != 2 {
		t.Errorf("noop filter returned %d entries, want 2", len(result))
	}
}

func TestApplyDateRange(t *testing.T) {
	base := time.Date(2024, 3, 15, 12, 0, 0, 0, time.UTC)
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "/a", 200, base.Add(-2*time.Hour)),
		makeEntry("1.1.1.1", "/b", 200, base),
		makeEntry("1.1.1.1", "/c", 200, base.Add(2*time.Hour)),
	}

	tests := []struct {
		name     string
		from     time.Time
		to       time.Time
		wantLen  int
	}{
		{
			name:    "from only",
			from:    base.Add(-1 * time.Hour),
			wantLen: 2,
		},
		{
			name:    "to only",
			to:      base.Add(1 * time.Hour),
			wantLen: 2,
		},
		{
			name:    "from and to",
			from:    base.Add(-1 * time.Hour),
			to:      base.Add(1 * time.Hour),
			wantLen: 1,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dup := make([]parser.LogEntry, len(entries))
			copy(dup, entries)
			result := Apply(dup, Options{DateFrom: tt.from, DateTo: tt.to})
			if len(result) != tt.wantLen {
				t.Errorf("got %d entries, want %d", len(result), tt.wantLen)
			}
		})
	}
}

func TestApplyStatusRange(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "/a", 200, time.Time{}),
		makeEntry("1.1.1.1", "/b", 301, time.Time{}),
		makeEntry("1.1.1.1", "/c", 404, time.Time{}),
		makeEntry("1.1.1.1", "/d", 500, time.Time{}),
	}

	tests := []struct {
		name    string
		min     int
		max     int
		wantLen int
	}{
		{"4xx only", 400, 499, 1},
		{"4xx and 5xx", 400, 599, 2},
		{"min only", 400, 0, 2},
		{"max only", 0, 299, 1},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dup := make([]parser.LogEntry, len(entries))
			copy(dup, entries)
			result := Apply(dup, Options{StatusMin: tt.min, StatusMax: tt.max})
			if len(result) != tt.wantLen {
				t.Errorf("got %d entries, want %d", len(result), tt.wantLen)
			}
		})
	}
}

func TestApplyEndpointRegex(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "/api/v1/users", 200, time.Time{}),
		makeEntry("1.1.1.1", "/api/v2/orders", 200, time.Time{}),
		makeEntry("1.1.1.1", "/health", 200, time.Time{}),
		makeEntry("1.1.1.1", "/static/app.js", 200, time.Time{}),
	}

	tests := []struct {
		name    string
		regex   string
		wantLen int
	}{
		{"api endpoints", `^/api/`, 2},
		{"v1 only", `/v1/`, 1},
		{"all", `.*`, 4},
		{"static", `^/static/`, 1},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dup := make([]parser.LogEntry, len(entries))
			copy(dup, entries)
			re := regexp.MustCompile(tt.regex)
			result := Apply(dup, Options{EndpointRegex: re})
			if len(result) != tt.wantLen {
				t.Errorf("got %d entries, want %d", len(result), tt.wantLen)
			}
		})
	}
}

func TestApplyIPWhitelist(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "/a", 200, time.Time{}),
		makeEntry("2.2.2.2", "/b", 200, time.Time{}),
		makeEntry("3.3.3.3", "/c", 200, time.Time{}),
	}

	dup := make([]parser.LogEntry, len(entries))
	copy(dup, entries)
	result := Apply(dup, Options{IPWhitelist: map[string]bool{"1.1.1.1": true, "3.3.3.3": true}})
	if len(result) != 2 {
		t.Errorf("got %d entries, want 2", len(result))
	}
}

func TestApplyIPBlacklist(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "/a", 200, time.Time{}),
		makeEntry("2.2.2.2", "/b", 200, time.Time{}),
		makeEntry("3.3.3.3", "/c", 200, time.Time{}),
	}

	dup := make([]parser.LogEntry, len(entries))
	copy(dup, entries)
	result := Apply(dup, Options{IPBlacklist: map[string]bool{"2.2.2.2": true}})
	if len(result) != 2 {
		t.Errorf("got %d entries, want 2", len(result))
	}
}

func TestApplyCombinedFilters(t *testing.T) {
	base := time.Date(2024, 3, 15, 12, 0, 0, 0, time.UTC)
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "/api/users", 200, base),
		makeEntry("1.1.1.1", "/api/users", 500, base),
		makeEntry("2.2.2.2", "/api/users", 200, base),
		makeEntry("1.1.1.1", "/health", 200, base),
		makeEntry("1.1.1.1", "/api/users", 200, base.Add(-24*time.Hour)),
	}

	dup := make([]parser.LogEntry, len(entries))
	copy(dup, entries)
	result := Apply(dup, Options{
		DateFrom:      base.Add(-1 * time.Hour),
		StatusMin:     200,
		StatusMax:     299,
		EndpointRegex: regexp.MustCompile(`^/api/`),
		IPWhitelist:   map[string]bool{"1.1.1.1": true},
	})
	// Only the first entry passes all filters.
	if len(result) != 1 {
		t.Errorf("got %d entries, want 1", len(result))
	}
}
