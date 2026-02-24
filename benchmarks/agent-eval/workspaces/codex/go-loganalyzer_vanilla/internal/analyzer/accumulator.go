package analyzer

import (
	"container/heap"
	"math"
	"sort"
	"time"

	"go-loganalyzer/internal/model"
)

type metricsAccumulator struct {
	totalRequests int64
	skippedLines  int64

	uniqueIPs       map[string]struct{}
	uniqueEndpoints map[string]struct{}
	ipCounts        map[string]int
	endpointCounts  map[string]int
	statusCounts    map[string]int64

	slowest slowRequestHeap

	hourRequestCounts map[time.Time]int
	hourTotalCounts   map[time.Time]int
	hourErrorCounts   map[time.Time]int
}

func newAccumulator() *metricsAccumulator {
	acc := &metricsAccumulator{
		uniqueIPs:         map[string]struct{}{},
		uniqueEndpoints:   map[string]struct{}{},
		ipCounts:          map[string]int{},
		endpointCounts:    map[string]int{},
		statusCounts:      map[string]int64{"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0},
		hourRequestCounts: map[time.Time]int{},
		hourTotalCounts:   map[time.Time]int{},
		hourErrorCounts:   map[time.Time]int{},
	}
	heap.Init(&acc.slowest)
	return acc
}

func (m *metricsAccumulator) addEntry(entry model.Entry) {
	m.totalRequests++

	m.uniqueIPs[entry.IP] = struct{}{}
	m.ipCounts[entry.IP]++

	m.uniqueEndpoints[entry.Endpoint] = struct{}{}
	m.endpointCounts[entry.Endpoint]++

	switch {
	case entry.Status >= 200 && entry.Status < 300:
		m.statusCounts["2xx"]++
	case entry.Status >= 300 && entry.Status < 400:
		m.statusCounts["3xx"]++
	case entry.Status >= 400 && entry.Status < 500:
		m.statusCounts["4xx"]++
	case entry.Status >= 500 && entry.Status < 600:
		m.statusCounts["5xx"]++
	default:
		m.statusCounts["other"]++
	}

	if entry.HasResponseTime {
		heap.Push(&m.slowest, SlowRequest{
			Timestamp:      entry.Timestamp,
			IP:             entry.IP,
			Endpoint:       entry.Endpoint,
			Status:         entry.Status,
			ResponseTimeMs: entry.ResponseTimeMs,
		})
		if m.slowest.Len() > 10 {
			heap.Pop(&m.slowest)
		}
	}

	if !entry.Timestamp.IsZero() {
		hour := entry.Timestamp.UTC().Truncate(time.Hour)
		m.hourRequestCounts[hour]++
		m.hourTotalCounts[hour]++
		if entry.Status >= 400 {
			m.hourErrorCounts[hour]++
		}
	}
}

func (m *metricsAccumulator) markSkippedLine() {
	m.skippedLines++
}

func (m *metricsAccumulator) merge(other *metricsAccumulator) {
	m.totalRequests += other.totalRequests
	m.skippedLines += other.skippedLines

	for ip := range other.uniqueIPs {
		m.uniqueIPs[ip] = struct{}{}
	}
	for endpoint := range other.uniqueEndpoints {
		m.uniqueEndpoints[endpoint] = struct{}{}
	}
	for key, count := range other.ipCounts {
		m.ipCounts[key] += count
	}
	for key, count := range other.endpointCounts {
		m.endpointCounts[key] += count
	}
	for key, count := range other.statusCounts {
		m.statusCounts[key] += count
	}
	for key, count := range other.hourRequestCounts {
		m.hourRequestCounts[key] += count
	}
	for key, count := range other.hourTotalCounts {
		m.hourTotalCounts[key] += count
	}
	for key, count := range other.hourErrorCounts {
		m.hourErrorCounts[key] += count
	}

	for _, slow := range other.slowest {
		heap.Push(&m.slowest, slow)
		if m.slowest.Len() > 10 {
			heap.Pop(&m.slowest)
		}
	}
}

func (m *metricsAccumulator) snapshot() Metrics {
	status := map[string]StatusDistribution{}
	for _, key := range []string{"2xx", "3xx", "4xx", "5xx", "other"} {
		count := m.statusCounts[key]
		pct := 0.0
		if m.totalRequests > 0 {
			pct = float64(count) / float64(m.totalRequests) * 100
		}
		status[key] = StatusDistribution{
			Count:   count,
			Percent: round2(pct),
		}
	}

	return Metrics{
		TotalRequests:     m.totalRequests,
		UniqueIPs:         len(m.uniqueIPs),
		UniqueEndpoints:   len(m.uniqueEndpoints),
		SkippedLines:      m.skippedLines,
		StatusClasses:     status,
		TopIPs:            topNFromCounts(m.ipCounts, 10),
		TopEndpoints:      topNFromCounts(m.endpointCounts, 10),
		SlowestRequests:   m.slowest.sortedDesc(),
		RequestsPerHour:   sortedHourCounts(m.hourRequestCounts),
		ErrorRateOverTime: buildErrorRateSeries(m.hourTotalCounts, m.hourErrorCounts),
	}
}

type slowRequestHeap []SlowRequest

func (h slowRequestHeap) Len() int { return len(h) }

func (h slowRequestHeap) Less(i, j int) bool {
	return h[i].ResponseTimeMs < h[j].ResponseTimeMs
}

func (h slowRequestHeap) Swap(i, j int) {
	h[i], h[j] = h[j], h[i]
}

func (h *slowRequestHeap) Push(x any) {
	*h = append(*h, x.(SlowRequest))
}

func (h *slowRequestHeap) Pop() any {
	old := *h
	n := len(old)
	item := old[n-1]
	*h = old[:n-1]
	return item
}

func (h slowRequestHeap) sortedDesc() []SlowRequest {
	out := make([]SlowRequest, len(h))
	copy(out, h)
	sort.Slice(out, func(i, j int) bool {
		if out[i].ResponseTimeMs == out[j].ResponseTimeMs {
			return out[i].Timestamp.Before(out[j].Timestamp)
		}
		return out[i].ResponseTimeMs > out[j].ResponseTimeMs
	})
	return out
}

func topNFromCounts(counts map[string]int, n int) []RankedCount {
	out := make([]RankedCount, 0, len(counts))
	for value, count := range counts {
		out = append(out, RankedCount{Value: value, Count: count})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Count == out[j].Count {
			return out[i].Value < out[j].Value
		}
		return out[i].Count > out[j].Count
	})
	if len(out) > n {
		out = out[:n]
	}
	return out
}

func sortedHourCounts(input map[time.Time]int) []HourBucket {
	keys := make([]time.Time, 0, len(input))
	for key := range input {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i].Before(keys[j]) })

	out := make([]HourBucket, 0, len(keys))
	for _, key := range keys {
		out = append(out, HourBucket{
			Hour:  key,
			Count: input[key],
		})
	}
	return out
}

func buildErrorRateSeries(totalByHour, errorByHour map[time.Time]int) []ErrorRatePoint {
	keys := make([]time.Time, 0, len(totalByHour))
	for key := range totalByHour {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i].Before(keys[j]) })

	points := make([]ErrorRatePoint, 0, len(keys))
	rates := make([]float64, 0, len(keys))

	for _, hour := range keys {
		total := totalByHour[hour]
		errors := errorByHour[hour]
		rate := 0.0
		if total > 0 {
			rate = float64(errors) / float64(total) * 100
		}
		points = append(points, ErrorRatePoint{
			Hour:      hour,
			Total:     total,
			Errors:    errors,
			ErrorRate: round2(rate),
		})
		rates = append(rates, rate)
	}

	mean, stddev := meanStdDev(rates)
	threshold := mean + 2*stddev
	for i := range points {
		if points[i].Total >= 10 && points[i].ErrorRate > threshold {
			points[i].IsSpike = true
		}
	}
	return points
}

func meanStdDev(values []float64) (float64, float64) {
	if len(values) == 0 {
		return 0, 0
	}
	sum := 0.0
	for _, v := range values {
		sum += v
	}
	mean := sum / float64(len(values))

	varianceSum := 0.0
	for _, v := range values {
		delta := v - mean
		varianceSum += delta * delta
	}
	variance := varianceSum / float64(len(values))
	return mean, math.Sqrt(variance)
}

func round2(v float64) float64 {
	return math.Round(v*100) / 100
}
