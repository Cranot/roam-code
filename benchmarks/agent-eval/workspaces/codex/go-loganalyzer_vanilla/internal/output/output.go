package output

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"

	"go-loganalyzer/internal/analyzer"
)

func Write(w io.Writer, report analyzer.Report, format string) error {
	switch strings.ToLower(format) {
	case "text", "":
		return writeText(w, report)
	case "json":
		return writeJSON(w, report)
	case "csv":
		return writeCSV(w, report)
	default:
		return fmt.Errorf("unsupported output format: %s", format)
	}
}

func writeJSON(w io.Writer, report analyzer.Report) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(report)
}

func writeText(w io.Writer, report analyzer.Report) error {
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	defer tw.Flush()

	fmt.Fprintf(tw, "Analyzed At:\t%s\n", report.AnalyzedAt.Format(time.RFC3339))
	fmt.Fprintf(tw, "Files:\t%d\n\n", report.FileCount)

	fmt.Fprintln(tw, "Per-file Summary")
	fmt.Fprintln(tw, "FILE\tFORMAT\tTOTAL\tUNIQUE_IPS\tUNIQUE_ENDPOINTS\tSKIPPED")
	for _, file := range report.Files {
		fmt.Fprintf(
			tw,
			"%s\t%s\t%d\t%d\t%d\t%d\n",
			file.Path,
			file.Format,
			file.Metrics.TotalRequests,
			file.Metrics.UniqueIPs,
			file.Metrics.UniqueEndpoints,
			file.Metrics.SkippedLines,
		)
	}
	fmt.Fprintln(tw)

	fmt.Fprintln(tw, "Aggregate")
	writeMetricsText(tw, report.Aggregate)

	for _, file := range report.Files {
		fmt.Fprintln(tw)
		fmt.Fprintf(tw, "Details: %s\n", file.Path)
		writeMetricsText(tw, file.Metrics)
	}

	return nil
}

func writeMetricsText(w io.Writer, metrics analyzer.Metrics) {
	fmt.Fprintf(w, "Total Requests:\t%d\n", metrics.TotalRequests)
	fmt.Fprintf(w, "Unique IPs:\t%d\n", metrics.UniqueIPs)
	fmt.Fprintf(w, "Unique Endpoints:\t%d\n", metrics.UniqueEndpoints)
	fmt.Fprintf(w, "Skipped Lines:\t%d\n", metrics.SkippedLines)

	fmt.Fprintln(w, "Status Distribution")
	fmt.Fprintln(w, "CLASS\tCOUNT\tPERCENT")
	for _, class := range []string{"2xx", "3xx", "4xx", "5xx", "other"} {
		row := metrics.StatusClasses[class]
		fmt.Fprintf(w, "%s\t%d\t%.2f%%\n", class, row.Count, row.Percent)
	}

	fmt.Fprintln(w, "Top IPs")
	fmt.Fprintln(w, "IP\tCOUNT")
	for _, row := range metrics.TopIPs {
		fmt.Fprintf(w, "%s\t%d\n", row.Value, row.Count)
	}

	fmt.Fprintln(w, "Top Endpoints")
	fmt.Fprintln(w, "ENDPOINT\tCOUNT")
	for _, row := range metrics.TopEndpoints {
		fmt.Fprintf(w, "%s\t%d\n", row.Value, row.Count)
	}

	fmt.Fprintln(w, "Slowest Requests")
	fmt.Fprintln(w, "TIME\tIP\tENDPOINT\tSTATUS\tRESPONSE_MS")
	for _, row := range metrics.SlowestRequests {
		fmt.Fprintf(w, "%s\t%s\t%s\t%d\t%.2f\n", row.Timestamp.Format(time.RFC3339), row.IP, row.Endpoint, row.Status, row.ResponseTimeMs)
	}

	fmt.Fprintln(w, "Requests Per Hour")
	fmt.Fprintln(w, "HOUR\tCOUNT")
	for _, row := range metrics.RequestsPerHour {
		fmt.Fprintf(w, "%s\t%d\n", row.Hour.Format("2006-01-02 15:00"), row.Count)
	}

	fmt.Fprintln(w, "Error Rate Over Time")
	fmt.Fprintln(w, "HOUR\tTOTAL\tERRORS\tRATE\tSPIKE")
	for _, row := range metrics.ErrorRateOverTime {
		fmt.Fprintf(w, "%s\t%d\t%d\t%.2f%%\t%t\n", row.Hour.Format("2006-01-02 15:00"), row.Total, row.Errors, row.ErrorRate, row.IsSpike)
	}
}

func writeCSV(w io.Writer, report analyzer.Report) error {
	cw := csv.NewWriter(w)
	defer cw.Flush()

	header := []string{
		"scope",
		"file",
		"format",
		"total_requests",
		"unique_ips",
		"unique_endpoints",
		"skipped_lines",
		"status_2xx_count", "status_2xx_pct",
		"status_3xx_count", "status_3xx_pct",
		"status_4xx_count", "status_4xx_pct",
		"status_5xx_count", "status_5xx_pct",
		"status_other_count", "status_other_pct",
		"top_ips",
		"top_endpoints",
		"slowest_requests_ms",
		"requests_per_hour",
		"error_rate_over_time",
	}

	if err := cw.Write(header); err != nil {
		return err
	}

	if err := cw.Write(metricsCSVRow("aggregate", "", "aggregate", report.Aggregate)); err != nil {
		return err
	}
	for _, file := range report.Files {
		if err := cw.Write(metricsCSVRow("file", file.Path, file.Format, file.Metrics)); err != nil {
			return err
		}
	}
	return cw.Error()
}

func metricsCSVRow(scope, file, format string, metrics analyzer.Metrics) []string {
	row := []string{
		scope,
		file,
		format,
		strconv.FormatInt(metrics.TotalRequests, 10),
		strconv.Itoa(metrics.UniqueIPs),
		strconv.Itoa(metrics.UniqueEndpoints),
		strconv.FormatInt(metrics.SkippedLines, 10),
	}

	for _, class := range []string{"2xx", "3xx", "4xx", "5xx", "other"} {
		dist := metrics.StatusClasses[class]
		row = append(row, strconv.FormatInt(dist.Count, 10))
		row = append(row, fmt.Sprintf("%.2f", dist.Percent))
	}

	row = append(row, joinRanked(metrics.TopIPs))
	row = append(row, joinRanked(metrics.TopEndpoints))
	row = append(row, joinSlowest(metrics.SlowestRequests))
	row = append(row, joinHourBuckets(metrics.RequestsPerHour))
	row = append(row, joinErrorRates(metrics.ErrorRateOverTime))

	return row
}

func joinRanked(rows []analyzer.RankedCount) string {
	out := make([]string, 0, len(rows))
	for _, row := range rows {
		out = append(out, fmt.Sprintf("%s:%d", row.Value, row.Count))
	}
	return strings.Join(out, "|")
}

func joinSlowest(rows []analyzer.SlowRequest) string {
	out := make([]string, 0, len(rows))
	for _, row := range rows {
		out = append(out, fmt.Sprintf("%s,%s,%.2f", row.IP, row.Endpoint, row.ResponseTimeMs))
	}
	return strings.Join(out, "|")
}

func joinHourBuckets(rows []analyzer.HourBucket) string {
	out := make([]string, 0, len(rows))
	for _, row := range rows {
		out = append(out, fmt.Sprintf("%s:%d", row.Hour.Format("2006-01-02 15:00"), row.Count))
	}
	return strings.Join(out, "|")
}

func joinErrorRates(rows []analyzer.ErrorRatePoint) string {
	out := make([]string, 0, len(rows))
	for _, row := range rows {
		out = append(out, fmt.Sprintf("%s,%.2f,%t", row.Hour.Format("2006-01-02 15:00"), row.ErrorRate, row.IsSpike))
	}
	return strings.Join(out, "|")
}
