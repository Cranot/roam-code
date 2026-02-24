package output

import (
	"encoding/csv"
	"fmt"
	"io"

	"github.com/example/loganalyzer/internal/analyzer"
)

// CSVFormatter formats output as CSV
type CSVFormatter struct {
	writer io.Writer
}

// NewCSVFormatter creates a new CSV formatter
func NewCSVFormatter(w io.Writer) *CSVFormatter {
	return &CSVFormatter{writer: w}
}

// Format formats statistics as CSV
func (f *CSVFormatter) Format(stats *analyzer.Statistics, skipped, total int) error {
	csvWriter := csv.NewWriter(f.writer)
	defer csvWriter.Flush()

	// Summary section
	if err := csvWriter.Write([]string{"Metric", "Value"}); err != nil {
		return err
	}

	summaryRows := [][]string{
		{"Total Lines", fmt.Sprintf("%d", total)},
		{"Valid Requests", fmt.Sprintf("%d", stats.TotalRequests)},
		{"Skipped Lines", fmt.Sprintf("%d", skipped)},
		{"Unique IPs", fmt.Sprintf("%d", len(stats.UniqueIPs))},
		{"Unique Endpoints", fmt.Sprintf("%d", len(stats.UniqueEndpoints))},
	}

	for _, row := range summaryRows {
		if err := csvWriter.Write(row); err != nil {
			return err
		}
	}

	// Empty row
	csvWriter.Write([]string{})

	// Status distribution
	csvWriter.Write([]string{"Status Range", "Count", "Percentage"})
	if stats.TotalRequests > 0 {
		statusRows := [][]string{
			{"2xx", fmt.Sprintf("%d", stats.Status2xx),
				fmt.Sprintf("%.2f", float64(stats.Status2xx)/float64(stats.TotalRequests)*100)},
			{"3xx", fmt.Sprintf("%d", stats.Status3xx),
				fmt.Sprintf("%.2f", float64(stats.Status3xx)/float64(stats.TotalRequests)*100)},
			{"4xx", fmt.Sprintf("%d", stats.Status4xx),
				fmt.Sprintf("%.2f", float64(stats.Status4xx)/float64(stats.TotalRequests)*100)},
			{"5xx", fmt.Sprintf("%d", stats.Status5xx),
				fmt.Sprintf("%.2f", float64(stats.Status5xx)/float64(stats.TotalRequests)*100)},
		}

		for _, row := range statusRows {
			if err := csvWriter.Write(row); err != nil {
				return err
			}
		}
	}

	// Empty row
	csvWriter.Write([]string{})

	// Top IPs
	csvWriter.Write([]string{"Top IPs", "Request Count"})
	topIPs := stats.TopIPs(10)
	for _, pair := range topIPs {
		if err := csvWriter.Write([]string{pair.Key, fmt.Sprintf("%d", pair.Value)}); err != nil {
			return err
		}
	}

	// Empty row
	csvWriter.Write([]string{})

	// Top Endpoints
	csvWriter.Write([]string{"Top Endpoints", "Request Count"})
	topEndpoints := stats.TopEndpoints(10)
	for _, pair := range topEndpoints {
		if err := csvWriter.Write([]string{pair.Key, fmt.Sprintf("%d", pair.Value)}); err != nil {
			return err
		}
	}

	// Empty row
	csvWriter.Write([]string{})

	// Slow requests
	if len(stats.SlowRequests) > 0 {
		csvWriter.Write([]string{"Slow Requests - IP", "Endpoint", "Response Time (ms)"})
		for _, entry := range stats.SlowRequests {
			if err := csvWriter.Write([]string{
				entry.IP,
				entry.Endpoint,
				fmt.Sprintf("%.2f", entry.ResponseTime),
			}); err != nil {
				return err
			}
		}
	}

	return nil
}
