package analyzer

import (
	"regexp"
	"testing"
	"time"

	"go-loganalyzer/internal/model"
)

func TestFiltersMatch(t *testing.T) {
	t.Parallel()

	from := time.Date(2025, 10, 10, 0, 0, 0, 0, time.UTC)
	to := time.Date(2025, 10, 11, 0, 0, 0, 0, time.UTC)
	re := regexp.MustCompile(`^/api`)

	filters := Filters{
		From:          &from,
		To:            &to,
		StatusMin:     200,
		StatusMax:     499,
		EndpointRegex: re,
		IPWhitelist:   map[string]struct{}{"10.0.0.1": {}},
		IPBlacklist:   map[string]struct{}{"10.0.0.9": {}},
	}

	tests := []struct {
		name  string
		entry model.Entry
		match bool
	}{
		{
			name: "matches all",
			entry: model.Entry{
				Timestamp: time.Date(2025, 10, 10, 12, 0, 0, 0, time.UTC),
				IP:        "10.0.0.1",
				Endpoint:  "/api/v1/users",
				Status:    200,
			},
			match: true,
		},
		{
			name: "blocked by status",
			entry: model.Entry{
				Timestamp: time.Date(2025, 10, 10, 12, 0, 0, 0, time.UTC),
				IP:        "10.0.0.1",
				Endpoint:  "/api/v1/users",
				Status:    500,
			},
			match: false,
		},
		{
			name: "blocked by regex",
			entry: model.Entry{
				Timestamp: time.Date(2025, 10, 10, 12, 0, 0, 0, time.UTC),
				IP:        "10.0.0.1",
				Endpoint:  "/static/logo.png",
				Status:    200,
			},
			match: false,
		},
		{
			name: "blocked by whitelist",
			entry: model.Entry{
				Timestamp: time.Date(2025, 10, 10, 12, 0, 0, 0, time.UTC),
				IP:        "10.0.0.2",
				Endpoint:  "/api/v1/users",
				Status:    200,
			},
			match: false,
		},
		{
			name: "blocked by blacklist",
			entry: model.Entry{
				Timestamp: time.Date(2025, 10, 10, 12, 0, 0, 0, time.UTC),
				IP:        "10.0.0.9",
				Endpoint:  "/api/v1/users",
				Status:    200,
			},
			match: false,
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := filters.Match(tt.entry)
			if got != tt.match {
				t.Fatalf("Match()=%t, want %t", got, tt.match)
			}
		})
	}
}

func TestAccumulatorSnapshot(t *testing.T) {
	t.Parallel()

	acc := newAccumulator()

	entries := []model.Entry{
		{
			Timestamp:       time.Date(2025, 10, 10, 10, 5, 0, 0, time.UTC),
			IP:              "10.0.0.1",
			Endpoint:        "/a",
			Status:          200,
			ResponseTimeMs:  100,
			HasResponseTime: true,
		},
		{
			Timestamp:       time.Date(2025, 10, 10, 10, 20, 0, 0, time.UTC),
			IP:              "10.0.0.2",
			Endpoint:        "/a",
			Status:          500,
			ResponseTimeMs:  200,
			HasResponseTime: true,
		},
		{
			Timestamp: time.Date(2025, 10, 10, 11, 0, 0, 0, time.UTC),
			IP:        "10.0.0.1",
			Endpoint:  "/b",
			Status:    404,
		},
	}

	for _, entry := range entries {
		acc.addEntry(entry)
	}
	acc.markSkippedLine()

	metrics := acc.snapshot()

	if metrics.TotalRequests != 3 {
		t.Fatalf("TotalRequests=%d, want 3", metrics.TotalRequests)
	}
	if metrics.UniqueIPs != 2 {
		t.Fatalf("UniqueIPs=%d, want 2", metrics.UniqueIPs)
	}
	if metrics.UniqueEndpoints != 2 {
		t.Fatalf("UniqueEndpoints=%d, want 2", metrics.UniqueEndpoints)
	}
	if metrics.SkippedLines != 1 {
		t.Fatalf("SkippedLines=%d, want 1", metrics.SkippedLines)
	}
	if len(metrics.TopIPs) == 0 || metrics.TopIPs[0].Value != "10.0.0.1" {
		t.Fatalf("unexpected TopIPs: %#v", metrics.TopIPs)
	}
	if len(metrics.SlowestRequests) == 0 || metrics.SlowestRequests[0].ResponseTimeMs != 200 {
		t.Fatalf("unexpected SlowestRequests: %#v", metrics.SlowestRequests)
	}
}
