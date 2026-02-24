package output

import (
	"encoding/csv"
	"fmt"
	"io"

	"github.com/loganalyzer/internal/analyzer"
)

// WriteCSV writes stats as CSV to w.
// Each file produces a section; aggregate is appended at the end.
func WriteCSV(w io.Writer, fileStats []analyzer.Stats, aggregate *analyzer.Stats) error {
	cw := csv.NewWriter(w)
	defer cw.Flush()

	// Header.
	header := []string{
		"source", "total_requests", "unique_ips", "unique_endpoints",
		"2xx", "3xx", "4xx", "5xx", "other",
		"pct_2xx", "pct_3xx", "pct_4xx", "pct_5xx",
		"total_lines", "skipped_lines", "format",
	}
	if err := cw.Write(header); err != nil {
		return err
	}

	writeRow := func(s *analyzer.Stats, source string) error {
		row := []string{
			source,
			itoa(s.TotalRequests),
			itoa(s.UniqueIPs),
			itoa(s.UniqueEndpoints),
			itoa(s.StatusDist.Status2xx),
			itoa(s.StatusDist.Status3xx),
			itoa(s.StatusDist.Status4xx),
			itoa(s.StatusDist.Status5xx),
			itoa(s.StatusDist.Other),
			ftoa(s.StatusDist.Pct2xx),
			ftoa(s.StatusDist.Pct3xx),
			ftoa(s.StatusDist.Pct4xx),
			ftoa(s.StatusDist.Pct5xx),
			itoa(s.TotalLines),
			itoa(s.SkippedLines),
			s.Format,
		}
		return cw.Write(row)
	}

	for i := range fileStats {
		source := fileStats[i].SourceFile
		if source == "" {
			source = fmt.Sprintf("file_%d", i+1)
		}
		if err := writeRow(&fileStats[i], source); err != nil {
			return err
		}
	}

	if aggregate != nil {
		if err := writeRow(aggregate, "AGGREGATE"); err != nil {
			return err
		}
	}

	return nil
}

func itoa(n int) string {
	return fmt.Sprintf("%d", n)
}

func ftoa(f float64) string {
	return fmt.Sprintf("%.1f", f)
}
