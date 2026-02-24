package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"regexp"
	"runtime"
	"strings"
	"sync/atomic"
	"time"

	"go-loganalyzer/internal/analyzer"
	"go-loganalyzer/internal/output"
)

type cliConfig struct {
	outputFormat  string
	workers       int
	noProgress    bool
	from          *time.Time
	to            *time.Time
	statusMin     int
	statusMax     int
	endpointRegex *regexp.Regexp
	ipWhitelist   map[string]struct{}
	ipBlacklist   map[string]struct{}
}

func main() {
	cfg, inputs, err := parseFlags()
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(2)
	}

	files, err := analyzer.DiscoverFiles(inputs)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error discovering files: %v\n", err)
		os.Exit(1)
	}
	if len(files) == 0 {
		fmt.Fprintln(os.Stderr, "no files found")
		os.Exit(1)
	}

	filters := analyzer.Filters{
		From:          cfg.from,
		To:            cfg.to,
		StatusMin:     cfg.statusMin,
		StatusMax:     cfg.statusMax,
		EndpointRegex: cfg.endpointRegex,
		IPWhitelist:   cfg.ipWhitelist,
		IPBlacklist:   cfg.ipBlacklist,
	}

	totalBytes := analyzer.TotalFileSize(files)
	var bytesRead atomic.Int64
	var filesDone atomic.Int64

	stopProgress := make(chan struct{})
	if !cfg.noProgress {
		go renderProgress(totalBytes, int64(len(files)), &bytesRead, &filesDone, stopProgress)
	}

	report, runErr := analyzer.AnalyzeFiles(context.Background(), files, filters, analyzer.Options{
		Workers: cfg.workers,
		ProgressFn: func(n int64) {
			bytesRead.Add(n)
		},
		FileDoneFn: func() {
			filesDone.Add(1)
		},
	})

	if !cfg.noProgress {
		close(stopProgress)
		fmt.Fprintln(os.Stderr)
	}

	if err := output.Write(os.Stdout, report, cfg.outputFormat); err != nil {
		fmt.Fprintf(os.Stderr, "error writing output: %v\n", err)
		os.Exit(1)
	}
	if runErr != nil {
		fmt.Fprintf(os.Stderr, "warning: %v\n", runErr)
		os.Exit(1)
	}
}

func parseFlags() (cliConfig, []string, error) {
	cfg := cliConfig{}

	var fromRaw string
	var toRaw string
	var endpointRegexRaw string
	var whitelistRaw string
	var blacklistRaw string

	flag.StringVar(&cfg.outputFormat, "output", "text", "Output format: text|json|csv")
	flag.IntVar(&cfg.workers, "workers", runtime.NumCPU(), "Number of worker goroutines")
	flag.BoolVar(&cfg.noProgress, "no-progress", false, "Disable progress bar")
	flag.StringVar(&fromRaw, "from", "", "Start timestamp (RFC3339 or YYYY-MM-DD)")
	flag.StringVar(&toRaw, "to", "", "End timestamp (RFC3339 or YYYY-MM-DD)")
	flag.IntVar(&cfg.statusMin, "status-min", 0, "Minimum status code filter")
	flag.IntVar(&cfg.statusMax, "status-max", 999, "Maximum status code filter")
	flag.StringVar(&endpointRegexRaw, "endpoint-regex", "", "Regex to filter endpoints")
	flag.StringVar(&whitelistRaw, "ip-whitelist", "", "Comma-separated list of allowed IPs")
	flag.StringVar(&blacklistRaw, "ip-blacklist", "", "Comma-separated list of blocked IPs")
	flag.Parse()

	if flag.NArg() == 0 {
		return cfg, nil, fmt.Errorf("provide at least one file or directory input")
	}
	if cfg.workers <= 0 {
		return cfg, nil, fmt.Errorf("workers must be > 0")
	}
	if cfg.statusMin < 0 || cfg.statusMax < 0 || cfg.statusMin > cfg.statusMax {
		return cfg, nil, fmt.Errorf("invalid status range")
	}

	var err error
	cfg.from, err = parseTimeFlag(fromRaw, false)
	if err != nil {
		return cfg, nil, fmt.Errorf("invalid --from value: %w", err)
	}
	cfg.to, err = parseTimeFlag(toRaw, true)
	if err != nil {
		return cfg, nil, fmt.Errorf("invalid --to value: %w", err)
	}

	if strings.TrimSpace(endpointRegexRaw) != "" {
		cfg.endpointRegex, err = regexp.Compile(endpointRegexRaw)
		if err != nil {
			return cfg, nil, fmt.Errorf("invalid --endpoint-regex: %w", err)
		}
	}

	cfg.ipWhitelist = analyzer.ParseIPList(whitelistRaw)
	cfg.ipBlacklist = analyzer.ParseIPList(blacklistRaw)
	return cfg, flag.Args(), nil
}

func parseTimeFlag(raw string, endOfDay bool) (*time.Time, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}

	layouts := []string{
		time.RFC3339Nano,
		time.RFC3339,
		"2006-01-02 15:04:05",
		"2006-01-02",
	}
	for _, layout := range layouts {
		ts, err := time.Parse(layout, raw)
		if err != nil {
			continue
		}
		if layout == "2006-01-02" && endOfDay {
			ts = ts.Add(23*time.Hour + 59*time.Minute + 59*time.Second)
		}
		return &ts, nil
	}
	return nil, fmt.Errorf("unsupported time format: %s", raw)
}

func renderProgress(totalBytes, totalFiles int64, bytesRead, filesDone *atomic.Int64, stop <-chan struct{}) {
	if totalBytes <= 0 {
		totalBytes = 1
	}
	ticker := time.NewTicker(120 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-stop:
			drawProgress(totalBytes, totalFiles, bytesRead.Load(), filesDone.Load())
			return
		case <-ticker.C:
			drawProgress(totalBytes, totalFiles, bytesRead.Load(), filesDone.Load())
		}
	}
}

func drawProgress(totalBytes, totalFiles, currentBytes, doneFiles int64) {
	const width = 32

	if currentBytes > totalBytes {
		currentBytes = totalBytes
	}
	pct := float64(currentBytes) / float64(totalBytes)
	filled := int(pct * width)
	if filled > width {
		filled = width
	}

	bar := strings.Repeat("#", filled) + strings.Repeat("-", width-filled)
	fmt.Fprintf(
		os.Stderr,
		"\r[%s] %6.2f%%  %s/%s  files %d/%d",
		bar,
		pct*100,
		formatBytes(currentBytes),
		formatBytes(totalBytes),
		doneFiles,
		totalFiles,
	)
}

func formatBytes(b int64) string {
	const unit = 1024
	if b < unit {
		return fmt.Sprintf("%dB", b)
	}
	div, exp := int64(unit), 0
	for n := b / unit; n >= unit; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f%ciB", float64(b)/float64(div), "KMGTPE"[exp])
}
