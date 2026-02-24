package analyzer

import (
	"testing"
	"time"

	"github.com/example/loganalyzer/internal/parser"
)

func TestDateRangeFilter(t *testing.T) {
	filters := NewFilters()

	start := time.Date(2023, 11, 15, 0, 0, 0, 0, time.UTC)
	end := time.Date(2023, 11, 16, 0, 0, 0, 0, time.UTC)
	filters.SetDateRange(&start, &end)

	tests := []struct {
		name      string
		timestamp time.Time
		wantMatch bool
	}{
		{
			name:      "before range",
			timestamp: time.Date(2023, 11, 14, 12, 0, 0, 0, time.UTC),
			wantMatch: false,
		},
		{
			name:      "within range",
			timestamp: time.Date(2023, 11, 15, 12, 0, 0, 0, time.UTC),
			wantMatch: true,
		},
		{
			name:      "after range",
			timestamp: time.Date(2023, 11, 17, 12, 0, 0, 0, time.UTC),
			wantMatch: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry := &parser.LogEntry{
				Timestamp:  tt.timestamp,
				IP:         "127.0.0.1",
				StatusCode: 200,
			}

			if got := filters.Matches(entry); got != tt.wantMatch {
				t.Errorf("Matches() = %v, want %v", got, tt.wantMatch)
			}
		})
	}
}

func TestStatusRangeFilter(t *testing.T) {
	filters := NewFilters()
	filters.SetStatusRange(400, 499)

	tests := []struct {
		name       string
		statusCode int
		wantMatch  bool
	}{
		{"below range", 200, false},
		{"within range", 404, true},
		{"above range", 500, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry := &parser.LogEntry{
				IP:         "127.0.0.1",
				StatusCode: tt.statusCode,
				Timestamp:  time.Now(),
			}

			if got := filters.Matches(entry); got != tt.wantMatch {
				t.Errorf("Matches() = %v, want %v", got, tt.wantMatch)
			}
		})
	}
}

func TestEndpointRegexFilter(t *testing.T) {
	filters := NewFilters()
	err := filters.SetEndpointPattern("^/api/")
	if err != nil {
		t.Fatalf("SetEndpointPattern() error = %v", err)
	}

	tests := []struct {
		name      string
		endpoint  string
		wantMatch bool
	}{
		{"matches pattern", "/api/users", true},
		{"does not match", "/home", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry := &parser.LogEntry{
				IP:         "127.0.0.1",
				Endpoint:   tt.endpoint,
				StatusCode: 200,
				Timestamp:  time.Now(),
			}

			if got := filters.Matches(entry); got != tt.wantMatch {
				t.Errorf("Matches() = %v, want %v", got, tt.wantMatch)
			}
		})
	}
}

func TestIPWhitelistFilter(t *testing.T) {
	filters := NewFilters()
	filters.AddIPWhitelist([]string{"192.168.1.1", "192.168.1.2"})

	tests := []struct {
		name      string
		ip        string
		wantMatch bool
	}{
		{"whitelisted IP 1", "192.168.1.1", true},
		{"whitelisted IP 2", "192.168.1.2", true},
		{"non-whitelisted IP", "10.0.0.1", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry := &parser.LogEntry{
				IP:         tt.ip,
				StatusCode: 200,
				Timestamp:  time.Now(),
			}

			if got := filters.Matches(entry); got != tt.wantMatch {
				t.Errorf("Matches() = %v, want %v", got, tt.wantMatch)
			}
		})
	}
}

func TestIPBlacklistFilter(t *testing.T) {
	filters := NewFilters()
	filters.AddIPBlacklist([]string{"10.0.0.1", "10.0.0.2"})

	tests := []struct {
		name      string
		ip        string
		wantMatch bool
	}{
		{"blacklisted IP 1", "10.0.0.1", false},
		{"blacklisted IP 2", "10.0.0.2", false},
		{"non-blacklisted IP", "192.168.1.1", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry := &parser.LogEntry{
				IP:         tt.ip,
				StatusCode: 200,
				Timestamp:  time.Now(),
			}

			if got := filters.Matches(entry); got != tt.wantMatch {
				t.Errorf("Matches() = %v, want %v", got, tt.wantMatch)
			}
		})
	}
}
