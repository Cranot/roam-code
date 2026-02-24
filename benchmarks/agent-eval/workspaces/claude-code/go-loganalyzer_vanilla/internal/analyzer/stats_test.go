package analyzer

import (
	"testing"
	"time"

	"github.com/loganalyzer/internal/parser"
)

func makeEntry(ip, method, path string, status int, ts time.Time, rt float64) parser.LogEntry {
	return parser.LogEntry{
		RemoteAddr:   ip,
		Method:       method,
		Path:         path,
		StatusCode:   status,
		Timestamp:    ts,
		ResponseTime: rt,
		SourceFile:   "test.log",
		LineNumber:   1,
	}
}

func TestComputeEmpty(t *testing.T) {
	stats := Compute(nil)
	if stats.TotalRequests != 0 {
		t.Errorf("TotalRequests = %d, want 0", stats.TotalRequests)
	}
}

func TestComputeStatusDistribution(t *testing.T) {
	tests := []struct {
		name    string
		entries []parser.LogEntry
		want    StatusDistribution
	}{
		{
			name: "all 2xx",
			entries: []parser.LogEntry{
				makeEntry("1.1.1.1", "GET", "/a", 200, time.Time{}, 0),
				makeEntry("1.1.1.1", "GET", "/b", 201, time.Time{}, 0),
			},
			want: StatusDistribution{Status2xx: 2, Pct2xx: 100},
		},
		{
			name: "mixed statuses",
			entries: []parser.LogEntry{
				makeEntry("1.1.1.1", "GET", "/a", 200, time.Time{}, 0),
				makeEntry("1.1.1.2", "GET", "/b", 301, time.Time{}, 0),
				makeEntry("1.1.1.3", "GET", "/c", 404, time.Time{}, 0),
				makeEntry("1.1.1.4", "GET", "/d", 500, time.Time{}, 0),
			},
			want: StatusDistribution{
				Status2xx: 1, Pct2xx: 25,
				Status3xx: 1, Pct3xx: 25,
				Status4xx: 1, Pct4xx: 25,
				Status5xx: 1, Pct5xx: 25,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stats := Compute(tt.entries)
			d := stats.StatusDist
			if d.Status2xx != tt.want.Status2xx || d.Status3xx != tt.want.Status3xx ||
				d.Status4xx != tt.want.Status4xx || d.Status5xx != tt.want.Status5xx {
				t.Errorf("counts = %d/%d/%d/%d, want %d/%d/%d/%d",
					d.Status2xx, d.Status3xx, d.Status4xx, d.Status5xx,
					tt.want.Status2xx, tt.want.Status3xx, tt.want.Status4xx, tt.want.Status5xx)
			}
			if d.Pct2xx != tt.want.Pct2xx {
				t.Errorf("Pct2xx = %f, want %f", d.Pct2xx, tt.want.Pct2xx)
			}
		})
	}
}

func TestComputeUniqueIPs(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "GET", "/a", 200, time.Time{}, 0),
		makeEntry("1.1.1.1", "GET", "/b", 200, time.Time{}, 0),
		makeEntry("2.2.2.2", "GET", "/c", 200, time.Time{}, 0),
		makeEntry("3.3.3.3", "GET", "/d", 200, time.Time{}, 0),
	}

	stats := Compute(entries)
	if stats.UniqueIPs != 3 {
		t.Errorf("UniqueIPs = %d, want 3", stats.UniqueIPs)
	}
}

func TestComputeUniqueEndpoints(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "GET", "/a", 200, time.Time{}, 0),
		makeEntry("1.1.1.1", "GET", "/a", 200, time.Time{}, 0),
		makeEntry("1.1.1.1", "POST", "/a", 200, time.Time{}, 0),
		makeEntry("1.1.1.1", "GET", "/b", 200, time.Time{}, 0),
	}

	stats := Compute(entries)
	// "GET /a", "POST /a", "GET /b" = 3 unique endpoints.
	if stats.UniqueEndpoints != 3 {
		t.Errorf("UniqueEndpoints = %d, want 3", stats.UniqueEndpoints)
	}
}

func TestComputeTopIPs(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "GET", "/a", 200, time.Time{}, 0),
		makeEntry("1.1.1.1", "GET", "/b", 200, time.Time{}, 0),
		makeEntry("1.1.1.1", "GET", "/c", 200, time.Time{}, 0),
		makeEntry("2.2.2.2", "GET", "/d", 200, time.Time{}, 0),
	}

	stats := Compute(entries)
	if len(stats.TopIPs) < 2 {
		t.Fatalf("TopIPs has %d items, want >= 2", len(stats.TopIPs))
	}
	if stats.TopIPs[0].Name != "1.1.1.1" || stats.TopIPs[0].Count != 3 {
		t.Errorf("TopIPs[0] = %v, want {1.1.1.1, 3}", stats.TopIPs[0])
	}
}

func TestComputeTopSlowest(t *testing.T) {
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "GET", "/fast", 200, time.Now(), 0.01),
		makeEntry("1.1.1.1", "GET", "/medium", 200, time.Now(), 0.5),
		makeEntry("1.1.1.1", "GET", "/slow", 200, time.Now(), 2.5),
		makeEntry("1.1.1.1", "GET", "/no-rt", 200, time.Now(), 0),
	}

	stats := Compute(entries)
	if len(stats.TopSlowest) != 3 {
		t.Fatalf("TopSlowest has %d items, want 3", len(stats.TopSlowest))
	}
	if stats.TopSlowest[0].Path != "/slow" {
		t.Errorf("TopSlowest[0].Path = %q, want /slow", stats.TopSlowest[0].Path)
	}
	if stats.TopSlowest[0].ResponseTime != 2.5 {
		t.Errorf("TopSlowest[0].ResponseTime = %f, want 2.5", stats.TopSlowest[0].ResponseTime)
	}
}

func TestComputeRequestsPerHour(t *testing.T) {
	baseTime := time.Date(2024, 3, 15, 10, 0, 0, 0, time.UTC)
	entries := []parser.LogEntry{
		makeEntry("1.1.1.1", "GET", "/a", 200, baseTime, 0),
		makeEntry("1.1.1.1", "GET", "/b", 200, baseTime.Add(30*time.Minute), 0),
		makeEntry("1.1.1.1", "GET", "/c", 200, baseTime.Add(61*time.Minute), 0),
	}

	stats := Compute(entries)
	if len(stats.RequestsPerHour) != 2 {
		t.Fatalf("RequestsPerHour has %d buckets, want 2", len(stats.RequestsPerHour))
	}
	// First hour should have 2 requests.
	if stats.RequestsPerHour[0].Count != 2 {
		t.Errorf("RequestsPerHour[0].Count = %d, want 2", stats.RequestsPerHour[0].Count)
	}
}

func TestComputeErrorRateSpike(t *testing.T) {
	baseTime := time.Date(2024, 3, 15, 10, 0, 0, 0, time.UTC)
	var entries []parser.LogEntry

	// 10 hours of normal traffic (100 requests each, 5% error rate).
	for h := 0; h < 10; h++ {
		hourStart := baseTime.Add(time.Duration(h) * time.Hour)
		for i := 0; i < 95; i++ {
			entries = append(entries, makeEntry("1.1.1.1", "GET", "/ok", 200, hourStart.Add(time.Duration(i)*time.Second), 0))
		}
		for i := 0; i < 5; i++ {
			entries = append(entries, makeEntry("1.1.1.1", "GET", "/err", 500, hourStart.Add(time.Duration(95+i)*time.Second), 0))
		}
	}

	// 1 hour of spike (100 requests, 80% error rate).
	spikeHour := baseTime.Add(10 * time.Hour)
	for i := 0; i < 20; i++ {
		entries = append(entries, makeEntry("1.1.1.1", "GET", "/ok", 200, spikeHour.Add(time.Duration(i)*time.Second), 0))
	}
	for i := 0; i < 80; i++ {
		entries = append(entries, makeEntry("1.1.1.1", "GET", "/err", 500, spikeHour.Add(time.Duration(20+i)*time.Second), 0))
	}

	stats := Compute(entries)

	foundSpike := false
	for _, eb := range stats.ErrorRateTime {
		if eb.IsSpike {
			foundSpike = true
			if eb.ErrorRate < 50 {
				t.Errorf("spike bucket error rate = %f, want >= 50", eb.ErrorRate)
			}
		}
	}
	if !foundSpike {
		t.Error("expected to find a spike, found none")
	}
}

func TestMergeStats(t *testing.T) {
	s1 := Stats{
		TotalRequests: 100,
		UniqueIPs:     5,
		StatusDist:    StatusDistribution{Status2xx: 80, Status4xx: 20},
		TopIPs: []RankedItem{
			{Name: "1.1.1.1", Count: 50},
			{Name: "2.2.2.2", Count: 30},
		},
		TopEndpoints: []RankedItem{
			{Name: "GET /a", Count: 60},
		},
		RequestsPerHour: []HourBucket{
			{Hour: "2024-03-15T10", Count: 50},
			{Hour: "2024-03-15T11", Count: 50},
		},
		ErrorRateTime: []ErrorBucket{
			{Hour: "2024-03-15T10", Total: 50, Errors: 10},
			{Hour: "2024-03-15T11", Total: 50, Errors: 10},
		},
		SkippedLines: 5,
		TotalLines:   105,
	}

	s2 := Stats{
		TotalRequests: 50,
		UniqueIPs:     3,
		StatusDist:    StatusDistribution{Status2xx: 40, Status5xx: 10},
		TopIPs: []RankedItem{
			{Name: "1.1.1.1", Count: 20},
			{Name: "3.3.3.3", Count: 10},
		},
		TopEndpoints: []RankedItem{
			{Name: "GET /a", Count: 30},
		},
		RequestsPerHour: []HourBucket{
			{Hour: "2024-03-15T10", Count: 30},
		},
		ErrorRateTime: []ErrorBucket{
			{Hour: "2024-03-15T10", Total: 30, Errors: 5},
		},
		SkippedLines: 2,
		TotalLines:   52,
	}

	merged := MergeStats([]Stats{s1, s2})

	if merged.TotalRequests != 150 {
		t.Errorf("TotalRequests = %d, want 150", merged.TotalRequests)
	}
	if merged.SkippedLines != 7 {
		t.Errorf("SkippedLines = %d, want 7", merged.SkippedLines)
	}
	if merged.TotalLines != 157 {
		t.Errorf("TotalLines = %d, want 157", merged.TotalLines)
	}
	if merged.StatusDist.Status2xx != 120 {
		t.Errorf("Status2xx = %d, want 120", merged.StatusDist.Status2xx)
	}
	// IPs: 1.1.1.1(70), 2.2.2.2(30), 3.3.3.3(10) = 3 unique.
	if merged.UniqueIPs != 3 {
		t.Errorf("UniqueIPs = %d, want 3", merged.UniqueIPs)
	}
	if len(merged.TopIPs) < 1 || merged.TopIPs[0].Name != "1.1.1.1" {
		t.Errorf("TopIPs[0] = %v, want 1.1.1.1", merged.TopIPs)
	}
}

func TestTopN(t *testing.T) {
	counts := map[string]int{
		"a": 10, "b": 30, "c": 20, "d": 5,
	}
	result := topN(counts, 2)
	if len(result) != 2 {
		t.Fatalf("topN returned %d items, want 2", len(result))
	}
	if result[0].Name != "b" || result[0].Count != 30 {
		t.Errorf("topN[0] = %v, want {b, 30}", result[0])
	}
	if result[1].Name != "c" || result[1].Count != 20 {
		t.Errorf("topN[1] = %v, want {c, 20}", result[1])
	}
}

func TestPct(t *testing.T) {
	tests := []struct {
		count int
		total float64
		want  float64
	}{
		{50, 100, 50},
		{1, 3, 33.33333333333333},
		{0, 100, 0},
		{0, 0, 0},
	}

	for _, tt := range tests {
		got := pct(tt.count, tt.total)
		if got != tt.want {
			t.Errorf("pct(%d, %f) = %f, want %f", tt.count, tt.total, got, tt.want)
		}
	}
}

func TestSqrt(t *testing.T) {
	tests := []struct {
		input float64
		want  float64
	}{
		{4, 2},
		{9, 3},
		{0, 0},
		{-1, 0},
		{2, 1.4142135623730951},
	}

	for _, tt := range tests {
		got := sqrt(tt.input)
		if abs(got-tt.want) > 1e-10 {
			t.Errorf("sqrt(%f) = %f, want %f", tt.input, got, tt.want)
		}
	}
}
