package output

import (
	"fmt"
	"io"
	"strings"

	"github.com/example/loganalyzer/internal/analyzer"
)

// TextFormatter formats output as text tables
type TextFormatter struct {
	writer io.Writer
}

// NewTextFormatter creates a new text formatter
func NewTextFormatter(w io.Writer) *TextFormatter {
	return &TextFormatter{writer: w}
}

// Format formats statistics as text
func (f *TextFormatter) Format(stats *analyzer.Statistics, skipped, total int) error {
	// Summary
	fmt.Fprintf(f.writer, "=== Log Analysis Results ===\n\n")
	fmt.Fprintf(f.writer, "Total Lines Processed: %d\n", total)
	fmt.Fprintf(f.writer, "Valid Requests:        %d\n", stats.TotalRequests)
	fmt.Fprintf(f.writer, "Skipped Lines:         %d\n", skipped)
	fmt.Fprintf(f.writer, "Unique IPs:            %d\n", len(stats.UniqueIPs))
	fmt.Fprintf(f.writer, "Unique Endpoints:      %d\n\n", len(stats.UniqueEndpoints))

	// Status code distribution
	fmt.Fprintf(f.writer, "=== Status Code Distribution ===\n")
	if stats.TotalRequests > 0 {
		pct2xx := float64(stats.Status2xx) / float64(stats.TotalRequests) * 100
		pct3xx := float64(stats.Status3xx) / float64(stats.TotalRequests) * 100
		pct4xx := float64(stats.Status4xx) / float64(stats.TotalRequests) * 100
		pct5xx := float64(stats.Status5xx) / float64(stats.TotalRequests) * 100

		fmt.Fprintf(f.writer, "2xx (Success):      %6d (%.2f%%)\n", stats.Status2xx, pct2xx)
		fmt.Fprintf(f.writer, "3xx (Redirect):     %6d (%.2f%%)\n", stats.Status3xx, pct3xx)
		fmt.Fprintf(f.writer, "4xx (Client Error): %6d (%.2f%%)\n", stats.Status4xx, pct4xx)
		fmt.Fprintf(f.writer, "5xx (Server Error): %6d (%.2f%%)\n\n", stats.Status5xx, pct5xx)
	}

	// Top 10 IPs
	fmt.Fprintf(f.writer, "=== Top 10 IPs by Request Count ===\n")
	topIPs := stats.TopIPs(10)
	if len(topIPs) > 0 {
		fmt.Fprintf(f.writer, "%-15s %10s\n", "IP Address", "Requests")
		fmt.Fprintf(f.writer, "%s\n", strings.Repeat("-", 27))
		for _, pair := range topIPs {
			fmt.Fprintf(f.writer, "%-15s %10d\n", pair.Key, pair.Value)
		}
		fmt.Fprintf(f.writer, "\n")
	} else {
		fmt.Fprintf(f.writer, "No data available\n\n")
	}

	// Top 10 endpoints
	fmt.Fprintf(f.writer, "=== Top 10 Endpoints by Request Count ===\n")
	topEndpoints := stats.TopEndpoints(10)
	if len(topEndpoints) > 0 {
		fmt.Fprintf(f.writer, "%-50s %10s\n", "Endpoint", "Requests")
		fmt.Fprintf(f.writer, "%s\n", strings.Repeat("-", 62))
		for _, pair := range topEndpoints {
			endpoint := pair.Key
			if len(endpoint) > 50 {
				endpoint = endpoint[:47] + "..."
			}
			fmt.Fprintf(f.writer, "%-50s %10d\n", endpoint, pair.Value)
		}
		fmt.Fprintf(f.writer, "\n")
	} else {
		fmt.Fprintf(f.writer, "No data available\n\n")
	}

	// Top 10 slowest requests
	if len(stats.SlowRequests) > 0 {
		fmt.Fprintf(f.writer, "=== Top 10 Slowest Requests ===\n")
		fmt.Fprintf(f.writer, "%-15s %-50s %12s\n", "IP", "Endpoint", "Time (ms)")
		fmt.Fprintf(f.writer, "%s\n", strings.Repeat("-", 79))
		for _, entry := range stats.SlowRequests {
			endpoint := entry.Endpoint
			if len(endpoint) > 50 {
				endpoint = endpoint[:47] + "..."
			}
			fmt.Fprintf(f.writer, "%-15s %-50s %12.2f\n", entry.IP, endpoint, entry.ResponseTime)
		}
		fmt.Fprintf(f.writer, "\n")
	}

	// Hourly histogram
	histogram := stats.HourlyHistogram()
	if len(histogram) > 0 {
		fmt.Fprintf(f.writer, "=== Requests Per Hour ===\n")
		fmt.Fprintf(f.writer, "%-16s %10s\n", "Hour", "Requests")
		fmt.Fprintf(f.writer, "%s\n", strings.Repeat("-", 28))
		for _, bucket := range histogram {
			fmt.Fprintf(f.writer, "%-16s %10d\n", bucket.Hour, bucket.Requests)
		}
		fmt.Fprintf(f.writer, "\n")
	}

	// Error rate spikes
	spikes := stats.ErrorRateSpikes(2.0)
	if len(spikes) > 0 {
		fmt.Fprintf(f.writer, "=== Error Rate Spikes (>2x average) ===\n")
		fmt.Fprintf(f.writer, "%-16s %10s %10s %12s\n", "Hour", "Requests", "Errors", "Error Rate")
		fmt.Fprintf(f.writer, "%s\n", strings.Repeat("-", 52))
		for _, spike := range spikes {
			fmt.Fprintf(f.writer, "%-16s %10d %10d %11.2f%%\n",
				spike.Hour, spike.Requests, spike.Errors, spike.ErrorRate*100)
		}
		fmt.Fprintf(f.writer, "\n")
	}

	return nil
}
