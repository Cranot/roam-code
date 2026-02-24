package analyzer

import "time"

type RankedCount struct {
	Value string `json:"value"`
	Count int    `json:"count"`
}

type SlowRequest struct {
	Timestamp      time.Time `json:"timestamp"`
	IP             string    `json:"ip"`
	Endpoint       string    `json:"endpoint"`
	Status         int       `json:"status"`
	ResponseTimeMs float64   `json:"response_time_ms"`
}

type StatusDistribution struct {
	Count   int64   `json:"count"`
	Percent float64 `json:"percent"`
}

type HourBucket struct {
	Hour  time.Time `json:"hour"`
	Count int       `json:"count"`
}

type ErrorRatePoint struct {
	Hour      time.Time `json:"hour"`
	Total     int       `json:"total"`
	Errors    int       `json:"errors"`
	ErrorRate float64   `json:"error_rate"`
	IsSpike   bool      `json:"is_spike"`
}

type Metrics struct {
	TotalRequests     int64                         `json:"total_requests"`
	UniqueIPs         int                           `json:"unique_ips"`
	UniqueEndpoints   int                           `json:"unique_endpoints"`
	SkippedLines      int64                         `json:"skipped_lines"`
	StatusClasses     map[string]StatusDistribution `json:"status_classes"`
	TopIPs            []RankedCount                 `json:"top_ips"`
	TopEndpoints      []RankedCount                 `json:"top_endpoints"`
	SlowestRequests   []SlowRequest                 `json:"slowest_requests"`
	RequestsPerHour   []HourBucket                  `json:"requests_per_hour"`
	ErrorRateOverTime []ErrorRatePoint              `json:"error_rate_over_time"`
}

type FileReport struct {
	Path    string  `json:"path"`
	Format  string  `json:"format"`
	Metrics Metrics `json:"metrics"`
}

type Report struct {
	AnalyzedAt time.Time    `json:"analyzed_at"`
	FileCount  int          `json:"file_count"`
	Files      []FileReport `json:"files"`
	Aggregate  Metrics      `json:"aggregate"`
}
