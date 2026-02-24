package analyzer

import (
	"sort"
	"time"

	"github.com/loganalyzer/internal/parser"
)

// Stats holds computed statistics for a set of log entries.
type Stats struct {
	TotalRequests   int                `json:"total_requests"`
	UniqueIPs       int                `json:"unique_ips"`
	UniqueEndpoints int                `json:"unique_endpoints"`
	StatusDist      StatusDistribution `json:"status_distribution"`
	TopIPs          []RankedItem       `json:"top_ips"`
	TopEndpoints    []RankedItem       `json:"top_endpoints"`
	TopSlowest      []SlowRequest      `json:"top_slowest,omitempty"`
	RequestsPerHour []HourBucket       `json:"requests_per_hour"`
	ErrorRateTime   []ErrorBucket      `json:"error_rate_over_time"`
	SkippedLines    int                `json:"skipped_lines"`
	TotalLines      int                `json:"total_lines"`
	Format          string             `json:"format"`
	SourceFile      string             `json:"source_file,omitempty"`
}

// StatusDistribution holds counts and percentages for status code classes.
type StatusDistribution struct {
	Status2xx     int     `json:"2xx"`
	Status3xx     int     `json:"3xx"`
	Status4xx     int     `json:"4xx"`
	Status5xx     int     `json:"5xx"`
	Other         int     `json:"other"`
	Pct2xx        float64 `json:"pct_2xx"`
	Pct3xx        float64 `json:"pct_3xx"`
	Pct4xx        float64 `json:"pct_4xx"`
	Pct5xx        float64 `json:"pct_5xx"`
	PctOther      float64 `json:"pct_other"`
}

// RankedItem is a name/count pair for top-N lists.
type RankedItem struct {
	Name  string `json:"name"`
	Count int    `json:"count"`
}

// SlowRequest captures a slow request entry.
type SlowRequest struct {
	Method       string  `json:"method"`
	Path         string  `json:"path"`
	StatusCode   int     `json:"status_code"`
	ResponseTime float64 `json:"response_time_sec"`
	Timestamp    string  `json:"timestamp"`
	SourceFile   string  `json:"source_file"`
	LineNumber   int     `json:"line_number"`
}

// HourBucket is a count of requests within an hour.
type HourBucket struct {
	Hour  string `json:"hour"`
	Count int    `json:"count"`
}

// ErrorBucket tracks error rate in a time window.
type ErrorBucket struct {
	Hour       string  `json:"hour"`
	Total      int     `json:"total"`
	Errors     int     `json:"errors"`
	ErrorRate  float64 `json:"error_rate"`
	IsSpike    bool    `json:"is_spike"`
}

// Compute calculates statistics from a set of log entries.
func Compute(entries []parser.LogEntry) Stats {
	stats := Stats{
		TotalRequests: len(entries),
	}

	if len(entries) == 0 {
		return stats
	}

	ipCounts := make(map[string]int)
	endpointCounts := make(map[string]int)
	hourCounts := make(map[string]int)
	hourErrors := make(map[string]int)
	hourTotals := make(map[string]int)

	for i := range entries {
		e := &entries[i]

		// IPs.
		if e.RemoteAddr != "" {
			ipCounts[e.RemoteAddr]++
		}

		// Endpoints.
		endpoint := e.Method + " " + e.Path
		if e.Method == "" {
			endpoint = e.Path
		}
		if endpoint != "" && endpoint != " " {
			endpointCounts[endpoint]++
		}

		// Status distribution.
		switch {
		case e.StatusCode >= 200 && e.StatusCode < 300:
			stats.StatusDist.Status2xx++
		case e.StatusCode >= 300 && e.StatusCode < 400:
			stats.StatusDist.Status3xx++
		case e.StatusCode >= 400 && e.StatusCode < 500:
			stats.StatusDist.Status4xx++
		case e.StatusCode >= 500 && e.StatusCode < 600:
			stats.StatusDist.Status5xx++
		default:
			stats.StatusDist.Other++
		}

		// Hourly aggregation.
		if !e.Timestamp.IsZero() {
			hourKey := e.Timestamp.Truncate(time.Hour).Format("2006-01-02T15")
			hourCounts[hourKey]++
			hourTotals[hourKey]++
			if e.StatusCode >= 400 {
				hourErrors[hourKey]++
			}
		}
	}

	total := float64(stats.TotalRequests)
	stats.StatusDist.Pct2xx = pct(stats.StatusDist.Status2xx, total)
	stats.StatusDist.Pct3xx = pct(stats.StatusDist.Status3xx, total)
	stats.StatusDist.Pct4xx = pct(stats.StatusDist.Status4xx, total)
	stats.StatusDist.Pct5xx = pct(stats.StatusDist.Status5xx, total)
	stats.StatusDist.PctOther = pct(stats.StatusDist.Other, total)

	stats.UniqueIPs = len(ipCounts)
	stats.UniqueEndpoints = len(endpointCounts)

	stats.TopIPs = topN(ipCounts, 10)
	stats.TopEndpoints = topN(endpointCounts, 10)
	stats.TopSlowest = topSlowest(entries, 10)
	stats.RequestsPerHour = buildHourBuckets(hourCounts)
	stats.ErrorRateTime = buildErrorBuckets(hourTotals, hourErrors)

	return stats
}

func pct(count int, total float64) float64 {
	if total == 0 {
		return 0
	}
	return float64(count) / total * 100
}

func topN(counts map[string]int, n int) []RankedItem {
	items := make([]RankedItem, 0, len(counts))
	for name, count := range counts {
		items = append(items, RankedItem{Name: name, Count: count})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].Count != items[j].Count {
			return items[i].Count > items[j].Count
		}
		return items[i].Name < items[j].Name
	})
	if len(items) > n {
		items = items[:n]
	}
	return items
}

func topSlowest(entries []parser.LogEntry, n int) []SlowRequest {
	// Collect entries with response time > 0.
	var withRT []parser.LogEntry
	for i := range entries {
		if entries[i].ResponseTime > 0 {
			withRT = append(withRT, entries[i])
		}
	}
	if len(withRT) == 0 {
		return nil
	}

	sort.Slice(withRT, func(i, j int) bool {
		return withRT[i].ResponseTime > withRT[j].ResponseTime
	})

	if len(withRT) > n {
		withRT = withRT[:n]
	}

	result := make([]SlowRequest, len(withRT))
	for i, e := range withRT {
		result[i] = SlowRequest{
			Method:       e.Method,
			Path:         e.Path,
			StatusCode:   e.StatusCode,
			ResponseTime: e.ResponseTime,
			Timestamp:    e.Timestamp.Format(time.RFC3339),
			SourceFile:   e.SourceFile,
			LineNumber:   e.LineNumber,
		}
	}
	return result
}

func buildHourBuckets(hourCounts map[string]int) []HourBucket {
	buckets := make([]HourBucket, 0, len(hourCounts))
	for hour, count := range hourCounts {
		buckets = append(buckets, HourBucket{Hour: hour, Count: count})
	}
	sort.Slice(buckets, func(i, j int) bool {
		return buckets[i].Hour < buckets[j].Hour
	})
	return buckets
}

func buildErrorBuckets(totals, errors map[string]int) []ErrorBucket {
	buckets := make([]ErrorBucket, 0, len(totals))
	for hour, total := range totals {
		errCount := errors[hour]
		rate := 0.0
		if total > 0 {
			rate = float64(errCount) / float64(total) * 100
		}
		buckets = append(buckets, ErrorBucket{
			Hour:      hour,
			Total:     total,
			Errors:    errCount,
			ErrorRate: rate,
		})
	}
	sort.Slice(buckets, func(i, j int) bool {
		return buckets[i].Hour < buckets[j].Hour
	})

	// Detect spikes: error rate > mean + 2*stddev.
	if len(buckets) > 1 {
		var sum float64
		for _, b := range buckets {
			sum += b.ErrorRate
		}
		mean := sum / float64(len(buckets))
		var sqDiffSum float64
		for _, b := range buckets {
			diff := b.ErrorRate - mean
			sqDiffSum += diff * diff
		}
		stddev := 0.0
		if len(buckets) > 1 {
			stddev = sqrt(sqDiffSum / float64(len(buckets)))
		}
		threshold := mean + 2*stddev
		for i := range buckets {
			if buckets[i].ErrorRate > threshold && buckets[i].Errors > 0 {
				buckets[i].IsSpike = true
			}
		}
	}

	return buckets
}

func sqrt(x float64) float64 {
	if x <= 0 {
		return 0
	}
	// Newton's method.
	z := x / 2
	for i := 0; i < 100; i++ {
		z2 := z - (z*z-x)/(2*z)
		if abs(z2-z) < 1e-12 {
			return z2
		}
		z = z2
	}
	return z
}

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

// MergeStats combines multiple file-level Stats into one aggregate.
func MergeStats(fileStats []Stats) Stats {
	var allEntries []parser.LogEntry
	totalSkipped := 0
	totalLines := 0

	// We need the entries to recompute. Caller should provide them separately.
	// Instead, merge the computed stats directly.
	agg := Stats{}

	ipCounts := make(map[string]int)
	endpointCounts := make(map[string]int)
	hourCounts := make(map[string]int)
	hourTotals := make(map[string]int)
	hourErrors := make(map[string]int)

	_ = allEntries // We'll merge from stats, not re-iterate entries.

	for _, fs := range fileStats {
		agg.TotalRequests += fs.TotalRequests
		totalSkipped += fs.SkippedLines
		totalLines += fs.TotalLines

		agg.StatusDist.Status2xx += fs.StatusDist.Status2xx
		agg.StatusDist.Status3xx += fs.StatusDist.Status3xx
		agg.StatusDist.Status4xx += fs.StatusDist.Status4xx
		agg.StatusDist.Status5xx += fs.StatusDist.Status5xx
		agg.StatusDist.Other += fs.StatusDist.Other

		for _, item := range fs.TopIPs {
			ipCounts[item.Name] += item.Count
		}
		for _, item := range fs.TopEndpoints {
			endpointCounts[item.Name] += item.Count
		}
		for _, hb := range fs.RequestsPerHour {
			hourCounts[hb.Hour] += hb.Count
		}
		for _, eb := range fs.ErrorRateTime {
			hourTotals[eb.Hour] += eb.Total
			hourErrors[eb.Hour] += eb.Errors
		}
		agg.TopSlowest = append(agg.TopSlowest, fs.TopSlowest...)
	}

	agg.SkippedLines = totalSkipped
	agg.TotalLines = totalLines
	agg.UniqueIPs = len(ipCounts)
	agg.UniqueEndpoints = len(endpointCounts)

	total := float64(agg.TotalRequests)
	agg.StatusDist.Pct2xx = pct(agg.StatusDist.Status2xx, total)
	agg.StatusDist.Pct3xx = pct(agg.StatusDist.Status3xx, total)
	agg.StatusDist.Pct4xx = pct(agg.StatusDist.Status4xx, total)
	agg.StatusDist.Pct5xx = pct(agg.StatusDist.Status5xx, total)
	agg.StatusDist.PctOther = pct(agg.StatusDist.Other, total)

	agg.TopIPs = topN(ipCounts, 10)
	agg.TopEndpoints = topN(endpointCounts, 10)
	agg.RequestsPerHour = buildHourBuckets(hourCounts)
	agg.ErrorRateTime = buildErrorBuckets(hourTotals, hourErrors)

	// Top slowest across all files.
	sort.Slice(agg.TopSlowest, func(i, j int) bool {
		return agg.TopSlowest[i].ResponseTime > agg.TopSlowest[j].ResponseTime
	})
	if len(agg.TopSlowest) > 10 {
		agg.TopSlowest = agg.TopSlowest[:10]
	}

	return agg
}
