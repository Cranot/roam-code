package output

import (
	"fmt"
	"io"
	"strings"

	"github.com/loganalyzer/internal/analyzer"
)

// WriteTable writes stats as a formatted text table to w.
func WriteTable(w io.Writer, stats analyzer.Stats) {
	line := strings.Repeat("=", 70)
	thinLine := strings.Repeat("-", 70)

	if stats.SourceFile != "" {
		fmt.Fprintf(w, "\n%s\n  File: %s\n%s\n", line, stats.SourceFile, line)
	} else {
		fmt.Fprintf(w, "\n%s\n  AGGREGATE STATISTICS\n%s\n", line, line)
	}

	if stats.Format != "" {
		fmt.Fprintf(w, "  Detected format: %s\n", stats.Format)
	}

	fmt.Fprintf(w, "\n  Summary\n%s\n", thinLine)
	fmt.Fprintf(w, "  %-30s %d\n", "Total requests:", stats.TotalRequests)
	fmt.Fprintf(w, "  %-30s %d\n", "Unique IPs:", stats.UniqueIPs)
	fmt.Fprintf(w, "  %-30s %d\n", "Unique endpoints:", stats.UniqueEndpoints)
	fmt.Fprintf(w, "  %-30s %d\n", "Total lines:", stats.TotalLines)
	fmt.Fprintf(w, "  %-30s %d\n", "Skipped (malformed) lines:", stats.SkippedLines)

	fmt.Fprintf(w, "\n  Status Code Distribution\n%s\n", thinLine)
	fmt.Fprintf(w, "  %-10s %8s %8s\n", "Class", "Count", "Percent")
	fmt.Fprintf(w, "  %-10s %8s %8s\n", "-----", "-----", "-------")
	writeStatusRow(w, "2xx", stats.StatusDist.Status2xx, stats.StatusDist.Pct2xx)
	writeStatusRow(w, "3xx", stats.StatusDist.Status3xx, stats.StatusDist.Pct3xx)
	writeStatusRow(w, "4xx", stats.StatusDist.Status4xx, stats.StatusDist.Pct4xx)
	writeStatusRow(w, "5xx", stats.StatusDist.Status5xx, stats.StatusDist.Pct5xx)
	if stats.StatusDist.Other > 0 {
		writeStatusRow(w, "other", stats.StatusDist.Other, stats.StatusDist.PctOther)
	}

	if len(stats.TopIPs) > 0 {
		fmt.Fprintf(w, "\n  Top IPs by Request Count\n%s\n", thinLine)
		fmt.Fprintf(w, "  %-5s %-40s %8s\n", "Rank", "IP", "Count")
		fmt.Fprintf(w, "  %-5s %-40s %8s\n", "----", "--", "-----")
		for i, item := range stats.TopIPs {
			fmt.Fprintf(w, "  %-5d %-40s %8d\n", i+1, item.Name, item.Count)
		}
	}

	if len(stats.TopEndpoints) > 0 {
		fmt.Fprintf(w, "\n  Top Endpoints by Request Count\n%s\n", thinLine)
		fmt.Fprintf(w, "  %-5s %-50s %8s\n", "Rank", "Endpoint", "Count")
		fmt.Fprintf(w, "  %-5s %-50s %8s\n", "----", "--------", "-----")
		for i, item := range stats.TopEndpoints {
			name := item.Name
			if len(name) > 50 {
				name = name[:47] + "..."
			}
			fmt.Fprintf(w, "  %-5d %-50s %8d\n", i+1, name, item.Count)
		}
	}

	if len(stats.TopSlowest) > 0 {
		fmt.Fprintf(w, "\n  Top 10 Slowest Requests\n%s\n", thinLine)
		fmt.Fprintf(w, "  %-5s %-6s %-35s %6s %10s\n", "Rank", "Method", "Path", "Status", "Time(s)")
		fmt.Fprintf(w, "  %-5s %-6s %-35s %6s %10s\n", "----", "------", "----", "------", "-------")
		for i, sr := range stats.TopSlowest {
			path := sr.Path
			if len(path) > 35 {
				path = path[:32] + "..."
			}
			fmt.Fprintf(w, "  %-5d %-6s %-35s %6d %10.3f\n", i+1, sr.Method, path, sr.StatusCode, sr.ResponseTime)
		}
	}

	if len(stats.RequestsPerHour) > 0 {
		fmt.Fprintf(w, "\n  Requests Per Hour\n%s\n", thinLine)
		fmt.Fprintf(w, "  %-20s %8s  %s\n", "Hour", "Count", "Bar")
		fmt.Fprintf(w, "  %-20s %8s  %s\n", "----", "-----", "---")
		maxCount := 0
		for _, hb := range stats.RequestsPerHour {
			if hb.Count > maxCount {
				maxCount = hb.Count
			}
		}
		for _, hb := range stats.RequestsPerHour {
			barLen := 0
			if maxCount > 0 {
				barLen = hb.Count * 40 / maxCount
			}
			if barLen < 1 && hb.Count > 0 {
				barLen = 1
			}
			bar := strings.Repeat("#", barLen)
			fmt.Fprintf(w, "  %-20s %8d  %s\n", hb.Hour, hb.Count, bar)
		}
	}

	if len(stats.ErrorRateTime) > 0 {
		hasSpike := false
		for _, eb := range stats.ErrorRateTime {
			if eb.IsSpike {
				hasSpike = true
				break
			}
		}
		fmt.Fprintf(w, "\n  Error Rate Over Time\n%s\n", thinLine)
		fmt.Fprintf(w, "  %-20s %8s %8s %10s %s\n", "Hour", "Total", "Errors", "Rate(%)", "Spike")
		fmt.Fprintf(w, "  %-20s %8s %8s %10s %s\n", "----", "-----", "------", "-------", "-----")
		for _, eb := range stats.ErrorRateTime {
			spike := ""
			if eb.IsSpike {
				spike = "***"
			}
			fmt.Fprintf(w, "  %-20s %8d %8d %9.1f%% %s\n", eb.Hour, eb.Total, eb.Errors, eb.ErrorRate, spike)
		}
		if hasSpike {
			fmt.Fprintf(w, "\n  *** = Error rate spike detected (>2 std deviations above mean)\n")
		}
	}

	fmt.Fprintln(w)
}

func writeStatusRow(w io.Writer, class string, count int, pct float64) {
	fmt.Fprintf(w, "  %-10s %8d %7.1f%%\n", class, count, pct)
}
