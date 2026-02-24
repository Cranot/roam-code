package analyzer

import (
	"testing"
	"time"

	"github.com/example/loganalyzer/internal/parser"
)

func TestStatisticsAddEntry(t *testing.T) {
	stats := NewStatistics()

	entry := &parser.LogEntry{
		IP:           "192.168.1.1",
		Endpoint:     "/api/test",
		StatusCode:   200,
		ResponseTime: 150.5,
		Timestamp:    time.Now(),
	}

	stats.AddEntry(entry)

	if stats.TotalRequests != 1 {
		t.Errorf("TotalRequests = %d, want 1", stats.TotalRequests)
	}

	if len(stats.UniqueIPs) != 1 {
		t.Errorf("UniqueIPs count = %d, want 1", len(stats.UniqueIPs))
	}

	if stats.Status2xx != 1 {
		t.Errorf("Status2xx = %d, want 1", stats.Status2xx)
	}
}

func TestStatisticsMerge(t *testing.T) {
	stats1 := NewStatistics()
	stats2 := NewStatistics()

	entry1 := &parser.LogEntry{
		IP:         "192.168.1.1",
		Endpoint:   "/api/test",
		StatusCode: 200,
		Timestamp:  time.Now(),
	}

	entry2 := &parser.LogEntry{
		IP:         "192.168.1.2",
		Endpoint:   "/api/test",
		StatusCode: 404,
		Timestamp:  time.Now(),
	}

	stats1.AddEntry(entry1)
	stats2.AddEntry(entry2)

	stats1.Merge(stats2)

	if stats1.TotalRequests != 2 {
		t.Errorf("TotalRequests = %d, want 2", stats1.TotalRequests)
	}

	if len(stats1.UniqueIPs) != 2 {
		t.Errorf("UniqueIPs count = %d, want 2", len(stats1.UniqueIPs))
	}

	if stats1.Status2xx != 1 {
		t.Errorf("Status2xx = %d, want 1", stats1.Status2xx)
	}

	if stats1.Status4xx != 1 {
		t.Errorf("Status4xx = %d, want 1", stats1.Status4xx)
	}
}

func TestTopN(t *testing.T) {
	m := map[string]int{
		"a": 10,
		"b": 30,
		"c": 20,
		"d": 5,
	}

	result := topN(m, 2)

	if len(result) != 2 {
		t.Fatalf("result length = %d, want 2", len(result))
	}

	if result[0].Key != "b" || result[0].Value != 30 {
		t.Errorf("result[0] = {%s, %d}, want {b, 30}", result[0].Key, result[0].Value)
	}

	if result[1].Key != "c" || result[1].Value != 20 {
		t.Errorf("result[1] = {%s, %d}, want {c, 20}", result[1].Key, result[1].Value)
	}
}

func TestStatusCodeCategorization(t *testing.T) {
	tests := []struct {
		name       string
		statusCode int
		want2xx    int
		want3xx    int
		want4xx    int
		want5xx    int
	}{
		{"2xx success", 200, 1, 0, 0, 0},
		{"3xx redirect", 301, 0, 1, 0, 0},
		{"4xx client error", 404, 0, 0, 1, 0},
		{"5xx server error", 500, 0, 0, 0, 1},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stats := NewStatistics()
			entry := &parser.LogEntry{
				IP:         "127.0.0.1",
				StatusCode: tt.statusCode,
				Timestamp:  time.Now(),
			}

			stats.AddEntry(entry)

			if stats.Status2xx != tt.want2xx {
				t.Errorf("Status2xx = %d, want %d", stats.Status2xx, tt.want2xx)
			}
			if stats.Status3xx != tt.want3xx {
				t.Errorf("Status3xx = %d, want %d", stats.Status3xx, tt.want3xx)
			}
			if stats.Status4xx != tt.want4xx {
				t.Errorf("Status4xx = %d, want %d", stats.Status4xx, tt.want4xx)
			}
			if stats.Status5xx != tt.want5xx {
				t.Errorf("Status5xx = %d, want %d", stats.Status5xx, tt.want5xx)
			}
		})
	}
}

func TestSlowRequests(t *testing.T) {
	stats := NewStatistics()

	// Add entries with different response times
	for i := 1; i <= 15; i++ {
		entry := &parser.LogEntry{
			IP:           "127.0.0.1",
			Endpoint:     "/test",
			StatusCode:   200,
			ResponseTime: float64(i * 10),
			Timestamp:    time.Now(),
		}
		stats.AddEntry(entry)
	}

	// Should keep only top 10 slowest
	if len(stats.SlowRequests) != 10 {
		t.Errorf("SlowRequests length = %d, want 10", len(stats.SlowRequests))
	}

	// Check that the slowest request is first
	if stats.SlowRequests[0].ResponseTime != 150 {
		t.Errorf("Slowest request time = %f, want 150", stats.SlowRequests[0].ResponseTime)
	}
}

func TestHourlyHistogram(t *testing.T) {
	stats := NewStatistics()

	// Add entries at different hours
	baseTime := time.Date(2023, 11, 15, 12, 0, 0, 0, time.UTC)

	for i := 0; i < 3; i++ {
		entry := &parser.LogEntry{
			IP:         "127.0.0.1",
			StatusCode: 200,
			Timestamp:  baseTime.Add(time.Duration(i) * time.Hour),
		}
		stats.AddEntry(entry)
		stats.AddEntry(entry) // Add twice
	}

	histogram := stats.HourlyHistogram()

	if len(histogram) != 3 {
		t.Errorf("histogram length = %d, want 3", len(histogram))
	}

	// Each hour should have 2 requests
	for _, bucket := range histogram {
		if bucket.Requests != 2 {
			t.Errorf("bucket %s requests = %d, want 2", bucket.Hour, bucket.Requests)
		}
	}
}
