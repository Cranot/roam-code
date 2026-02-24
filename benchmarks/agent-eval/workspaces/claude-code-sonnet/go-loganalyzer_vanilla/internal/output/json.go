package output

import (
	"encoding/json"
	"io"

	"github.com/example/loganalyzer/internal/analyzer"
)

// JSONFormatter formats output as JSON
type JSONFormatter struct {
	writer io.Writer
}

// NewJSONFormatter creates a new JSON formatter
func NewJSONFormatter(w io.Writer) *JSONFormatter {
	return &JSONFormatter{writer: w}
}

// Format formats statistics as JSON
func (f *JSONFormatter) Format(stats *analyzer.Statistics, skipped, total int) error {
	output := map[string]interface{}{
		"summary": map[string]interface{}{
			"total_lines":      total,
			"valid_requests":   stats.TotalRequests,
			"skipped_lines":    skipped,
			"unique_ips":       len(stats.UniqueIPs),
			"unique_endpoints": len(stats.UniqueEndpoints),
		},
		"status_distribution": map[string]interface{}{
			"2xx": map[string]interface{}{
				"count":      stats.Status2xx,
				"percentage": calculatePercentage(stats.Status2xx, stats.TotalRequests),
			},
			"3xx": map[string]interface{}{
				"count":      stats.Status3xx,
				"percentage": calculatePercentage(stats.Status3xx, stats.TotalRequests),
			},
			"4xx": map[string]interface{}{
				"count":      stats.Status4xx,
				"percentage": calculatePercentage(stats.Status4xx, stats.TotalRequests),
			},
			"5xx": map[string]interface{}{
				"count":      stats.Status5xx,
				"percentage": calculatePercentage(stats.Status5xx, stats.TotalRequests),
			},
		},
		"top_ips":       formatTopN(stats.TopIPs(10)),
		"top_endpoints": formatTopN(stats.TopEndpoints(10)),
	}

	// Add slow requests if available
	if len(stats.SlowRequests) > 0 {
		slowRequests := make([]map[string]interface{}, len(stats.SlowRequests))
		for i, entry := range stats.SlowRequests {
			slowRequests[i] = map[string]interface{}{
				"ip":            entry.IP,
				"endpoint":      entry.Endpoint,
				"response_time": entry.ResponseTime,
			}
		}
		output["slow_requests"] = slowRequests
	}

	// Add hourly histogram
	histogram := stats.HourlyHistogram()
	if len(histogram) > 0 {
		hourlyData := make([]map[string]interface{}, len(histogram))
		for i, bucket := range histogram {
			hourlyData[i] = map[string]interface{}{
				"hour":     bucket.Hour,
				"requests": bucket.Requests,
			}
		}
		output["hourly_histogram"] = hourlyData
	}

	// Add error spikes
	spikes := stats.ErrorRateSpikes(2.0)
	if len(spikes) > 0 {
		spikesData := make([]map[string]interface{}, len(spikes))
		for i, spike := range spikes {
			spikesData[i] = map[string]interface{}{
				"hour":       spike.Hour,
				"requests":   spike.Requests,
				"errors":     spike.Errors,
				"error_rate": spike.ErrorRate,
			}
		}
		output["error_spikes"] = spikesData
	}

	encoder := json.NewEncoder(f.writer)
	encoder.SetIndent("", "  ")
	return encoder.Encode(output)
}

func formatTopN(pairs []analyzer.KVPair) []map[string]interface{} {
	result := make([]map[string]interface{}, len(pairs))
	for i, pair := range pairs {
		result[i] = map[string]interface{}{
			"key":   pair.Key,
			"count": pair.Value,
		}
	}
	return result
}

func calculatePercentage(count, total int) float64 {
	if total == 0 {
		return 0
	}
	return float64(count) / float64(total) * 100
}
