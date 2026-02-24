package main

import (
	"flag"
	"fmt"
	"os"
	"regexp"
	"runtime"
	"strings"
	"time"

	"github.com/loganalyzer/internal/analyzer"
	"github.com/loganalyzer/internal/filter"
	"github.com/loganalyzer/internal/output"
	"github.com/loganalyzer/internal/worker"
)

const version = "1.0.0"

func main() {
	// Flags.
	workers := flag.Int("workers", runtime.NumCPU(), "number of concurrent workers")
	outputFmt := flag.String("format", "table", "output format: table, json, csv")
	dateFrom := flag.String("from", "", "filter: start date (RFC3339, e.g. 2024-01-01T00:00:00Z)")
	dateTo := flag.String("to", "", "filter: end date (RFC3339, e.g. 2024-12-31T23:59:59Z)")
	statusMin := flag.Int("status-min", 0, "filter: minimum status code (inclusive)")
	statusMax := flag.Int("status-max", 0, "filter: maximum status code (inclusive)")
	endpointRe := flag.String("endpoint", "", "filter: endpoint path regex")
	ipWhitelist := flag.String("ip-allow", "", "filter: comma-separated IP whitelist")
	ipBlacklist := flag.String("ip-block", "", "filter: comma-separated IP blacklist")
	noProgress := flag.Bool("no-progress", false, "disable progress bar")
	showVersion := flag.Bool("version", false, "show version and exit")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Usage: loganalyzer [options] <file-or-dir> [file-or-dir ...]\n\n")
		fmt.Fprintf(os.Stderr, "A concurrent log file analyzer supporting Apache Combined, Nginx, and JSON Lines formats.\n\n")
		fmt.Fprintf(os.Stderr, "Options:\n")
		flag.PrintDefaults()
		fmt.Fprintf(os.Stderr, "\nExamples:\n")
		fmt.Fprintf(os.Stderr, "  loganalyzer access.log\n")
		fmt.Fprintf(os.Stderr, "  loganalyzer -format json /var/log/nginx/\n")
		fmt.Fprintf(os.Stderr, "  loganalyzer -workers 8 -status-min 400 -format csv *.log\n")
		fmt.Fprintf(os.Stderr, "  loganalyzer -from 2024-01-01T00:00:00Z -endpoint '/api/.*' logs/\n")
	}

	flag.Parse()

	if *showVersion {
		fmt.Printf("loganalyzer %s\n", version)
		os.Exit(0)
	}

	if flag.NArg() == 0 {
		flag.Usage()
		os.Exit(1)
	}

	// Build filter options.
	filterOpts, err := buildFilterOpts(*dateFrom, *dateTo, *statusMin, *statusMax, *endpointRe, *ipWhitelist, *ipBlacklist)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}

	// Discover files.
	files, err := worker.DiscoverFiles(flag.Args())
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}

	if len(files) == 0 {
		fmt.Fprintf(os.Stderr, "No log files found in the specified paths.\n")
		os.Exit(1)
	}

	fmt.Fprintf(os.Stderr, "Found %d log file(s), processing with %d worker(s)...\n", len(files), *workers)

	// Set up worker pool.
	pool := &worker.Pool{
		Workers:    *workers,
		FilterOpts: filterOpts,
	}

	if !*noProgress && len(files) > 1 {
		pb := output.NewProgressBar(os.Stderr)
		pool.OnProgress = pb.Update
	}

	// Process files.
	start := time.Now()
	results := pool.Process(files)
	elapsed := time.Since(start)

	// Collect stats and report errors.
	var fileStats []analyzer.Stats
	var errCount int
	for _, r := range results {
		if r.Err != nil {
			fmt.Fprintf(os.Stderr, "Warning: %s: %v\n", r.FilePath, r.Err)
			errCount++
			continue
		}
		fileStats = append(fileStats, r.Stats)
	}

	if len(fileStats) == 0 {
		fmt.Fprintf(os.Stderr, "No files were successfully processed.\n")
		os.Exit(1)
	}

	// Compute aggregate.
	var aggregate *analyzer.Stats
	if len(fileStats) > 1 {
		agg := analyzer.MergeStats(fileStats)
		aggregate = &agg
	}

	// Output.
	switch strings.ToLower(*outputFmt) {
	case "json":
		if err := output.WriteJSON(os.Stdout, fileStats, aggregate); err != nil {
			fmt.Fprintf(os.Stderr, "Error writing JSON: %v\n", err)
			os.Exit(1)
		}
	case "csv":
		if err := output.WriteCSV(os.Stdout, fileStats, aggregate); err != nil {
			fmt.Fprintf(os.Stderr, "Error writing CSV: %v\n", err)
			os.Exit(1)
		}
	default:
		for _, fs := range fileStats {
			output.WriteTable(os.Stdout, fs)
		}
		if aggregate != nil {
			output.WriteTable(os.Stdout, *aggregate)
		}
	}

	fmt.Fprintf(os.Stderr, "\nCompleted in %v. Processed %d file(s), %d error(s).\n", elapsed.Round(time.Millisecond), len(fileStats), errCount)
}

func buildFilterOpts(dateFrom, dateTo string, statusMin, statusMax int, endpointRe, ipWhitelist, ipBlacklist string) (filter.Options, error) {
	opts := filter.Options{
		StatusMin: statusMin,
		StatusMax: statusMax,
	}

	if dateFrom != "" {
		t, err := time.Parse(time.RFC3339, dateFrom)
		if err != nil {
			return opts, fmt.Errorf("invalid -from date %q: %w", dateFrom, err)
		}
		opts.DateFrom = t
	}

	if dateTo != "" {
		t, err := time.Parse(time.RFC3339, dateTo)
		if err != nil {
			return opts, fmt.Errorf("invalid -to date %q: %w", dateTo, err)
		}
		opts.DateTo = t
	}

	if endpointRe != "" {
		re, err := regexp.Compile(endpointRe)
		if err != nil {
			return opts, fmt.Errorf("invalid -endpoint regex %q: %w", endpointRe, err)
		}
		opts.EndpointRegex = re
	}

	if ipWhitelist != "" {
		opts.IPWhitelist = make(map[string]bool)
		for _, ip := range strings.Split(ipWhitelist, ",") {
			ip = strings.TrimSpace(ip)
			if ip != "" {
				opts.IPWhitelist[ip] = true
			}
		}
	}

	if ipBlacklist != "" {
		opts.IPBlacklist = make(map[string]bool)
		for _, ip := range strings.Split(ipBlacklist, ",") {
			ip = strings.TrimSpace(ip)
			if ip != "" {
				opts.IPBlacklist[ip] = true
			}
		}
	}

	return opts, nil
}
