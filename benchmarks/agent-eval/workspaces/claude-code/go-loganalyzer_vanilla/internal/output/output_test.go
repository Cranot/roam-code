package output

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"strings"
	"testing"

	"github.com/loganalyzer/internal/analyzer"
)

func sampleStats() analyzer.Stats {
	return analyzer.Stats{
		TotalRequests:   100,
		UniqueIPs:       5,
		UniqueEndpoints: 10,
		StatusDist: analyzer.StatusDistribution{
			Status2xx: 80, Pct2xx: 80,
			Status3xx: 5, Pct3xx: 5,
			Status4xx: 10, Pct4xx: 10,
			Status5xx: 5, Pct5xx: 5,
		},
		TopIPs: []analyzer.RankedItem{
			{Name: "1.1.1.1", Count: 50},
			{Name: "2.2.2.2", Count: 30},
		},
		TopEndpoints: []analyzer.RankedItem{
			{Name: "GET /api/users", Count: 40},
			{Name: "POST /api/login", Count: 20},
		},
		TopSlowest: []analyzer.SlowRequest{
			{Method: "GET", Path: "/slow", StatusCode: 200, ResponseTime: 5.5, Timestamp: "2024-03-15T10:00:00Z"},
		},
		RequestsPerHour: []analyzer.HourBucket{
			{Hour: "2024-03-15T10", Count: 50},
			{Hour: "2024-03-15T11", Count: 50},
		},
		ErrorRateTime: []analyzer.ErrorBucket{
			{Hour: "2024-03-15T10", Total: 50, Errors: 5, ErrorRate: 10.0},
			{Hour: "2024-03-15T11", Total: 50, Errors: 10, ErrorRate: 20.0, IsSpike: true},
		},
		SkippedLines: 3,
		TotalLines:   103,
		Format:       "apache_combined",
		SourceFile:   "test.log",
	}
}

func TestWriteTable(t *testing.T) {
	var buf bytes.Buffer
	stats := sampleStats()
	WriteTable(&buf, stats)

	output := buf.String()

	// Check key sections exist.
	checks := []string{
		"test.log",
		"Total requests:",
		"100",
		"Unique IPs:",
		"Status Code Distribution",
		"2xx",
		"80.0%",
		"Top IPs",
		"1.1.1.1",
		"Top Endpoints",
		"GET /api/users",
		"Top 10 Slowest",
		"/slow",
		"5.500",
		"Requests Per Hour",
		"Error Rate Over Time",
		"***",
	}

	for _, check := range checks {
		if !strings.Contains(output, check) {
			t.Errorf("table output missing %q", check)
		}
	}
}

func TestWriteTableAggregate(t *testing.T) {
	var buf bytes.Buffer
	stats := sampleStats()
	stats.SourceFile = ""
	WriteTable(&buf, stats)
	if !strings.Contains(buf.String(), "AGGREGATE") {
		t.Error("expected AGGREGATE header for empty source file")
	}
}

func TestWriteJSON(t *testing.T) {
	var buf bytes.Buffer
	stats := sampleStats()
	err := WriteJSON(&buf, []analyzer.Stats{stats}, nil)
	if err != nil {
		t.Fatalf("WriteJSON error: %v", err)
	}

	var out JSONOutput
	if err := json.Unmarshal(buf.Bytes(), &out); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}

	if len(out.Files) != 1 {
		t.Errorf("JSON files count = %d, want 1", len(out.Files))
	}
	if out.Files[0].TotalRequests != 100 {
		t.Errorf("TotalRequests = %d, want 100", out.Files[0].TotalRequests)
	}
}

func TestWriteJSONWithAggregate(t *testing.T) {
	var buf bytes.Buffer
	stats := sampleStats()
	agg := sampleStats()
	agg.TotalRequests = 200
	err := WriteJSON(&buf, []analyzer.Stats{stats}, &agg)
	if err != nil {
		t.Fatalf("WriteJSON error: %v", err)
	}

	var out JSONOutput
	if err := json.Unmarshal(buf.Bytes(), &out); err != nil {
		t.Fatalf("invalid JSON: %v", err)
	}

	if out.Aggregate == nil {
		t.Fatal("expected aggregate, got nil")
	}
	if out.Aggregate.TotalRequests != 200 {
		t.Errorf("Aggregate.TotalRequests = %d, want 200", out.Aggregate.TotalRequests)
	}
}

func TestWriteCSV(t *testing.T) {
	var buf bytes.Buffer
	stats := sampleStats()
	err := WriteCSV(&buf, []analyzer.Stats{stats}, nil)
	if err != nil {
		t.Fatalf("WriteCSV error: %v", err)
	}

	r := csv.NewReader(strings.NewReader(buf.String()))
	records, err := r.ReadAll()
	if err != nil {
		t.Fatalf("invalid CSV: %v", err)
	}

	// Header + 1 data row.
	if len(records) != 2 {
		t.Errorf("CSV rows = %d, want 2", len(records))
	}

	header := records[0]
	if header[0] != "source" {
		t.Errorf("header[0] = %q, want %q", header[0], "source")
	}

	row := records[1]
	if row[0] != "test.log" {
		t.Errorf("row source = %q, want %q", row[0], "test.log")
	}
	if row[1] != "100" {
		t.Errorf("row total_requests = %q, want %q", row[1], "100")
	}
}

func TestWriteCSVWithAggregate(t *testing.T) {
	var buf bytes.Buffer
	stats := sampleStats()
	agg := sampleStats()
	err := WriteCSV(&buf, []analyzer.Stats{stats}, &agg)
	if err != nil {
		t.Fatalf("WriteCSV error: %v", err)
	}

	r := csv.NewReader(strings.NewReader(buf.String()))
	records, err := r.ReadAll()
	if err != nil {
		t.Fatalf("invalid CSV: %v", err)
	}

	// Header + 1 data row + 1 aggregate row.
	if len(records) != 3 {
		t.Errorf("CSV rows = %d, want 3", len(records))
	}
	if records[2][0] != "AGGREGATE" {
		t.Errorf("aggregate source = %q, want %q", records[2][0], "AGGREGATE")
	}
}

func TestProgressBar(t *testing.T) {
	var buf bytes.Buffer
	pb := NewProgressBar(&buf)

	pb.Update(1, 3, "file1.log")
	pb.Update(2, 3, "file2.log")
	pb.Update(3, 3, "file3.log")

	output := buf.String()
	if !strings.Contains(output, "100%") {
		t.Error("progress bar should show 100% at completion")
	}
	if !strings.Contains(output, "3/3") {
		t.Error("progress bar should show 3/3 at completion")
	}
}
