package filter

import (
	"regexp"
	"time"

	"github.com/loganalyzer/internal/parser"
)

// Options defines all available filter criteria.
type Options struct {
	// DateFrom filters entries on or after this time (zero = no lower bound).
	DateFrom time.Time
	// DateTo filters entries on or before this time (zero = no upper bound).
	DateTo time.Time
	// StatusMin is the minimum status code (inclusive, 0 = no filter).
	StatusMin int
	// StatusMax is the maximum status code (inclusive, 0 = no filter).
	StatusMax int
	// EndpointRegex filters entries whose path matches this pattern.
	EndpointRegex *regexp.Regexp
	// IPWhitelist, if non-empty, only allows these IPs.
	IPWhitelist map[string]bool
	// IPBlacklist, if non-empty, excludes these IPs.
	IPBlacklist map[string]bool
}

// Apply filters a slice of LogEntry in place, returning the filtered slice.
func Apply(entries []parser.LogEntry, opts Options) []parser.LogEntry {
	if isNoop(opts) {
		return entries
	}

	n := 0
	for i := range entries {
		if match(&entries[i], &opts) {
			entries[n] = entries[i]
			n++
		}
	}
	return entries[:n]
}

func isNoop(opts Options) bool {
	return opts.DateFrom.IsZero() &&
		opts.DateTo.IsZero() &&
		opts.StatusMin == 0 &&
		opts.StatusMax == 0 &&
		opts.EndpointRegex == nil &&
		len(opts.IPWhitelist) == 0 &&
		len(opts.IPBlacklist) == 0
}

func match(e *parser.LogEntry, opts *Options) bool {
	// Date range.
	if !opts.DateFrom.IsZero() && !e.Timestamp.IsZero() && e.Timestamp.Before(opts.DateFrom) {
		return false
	}
	if !opts.DateTo.IsZero() && !e.Timestamp.IsZero() && e.Timestamp.After(opts.DateTo) {
		return false
	}

	// Status code range.
	if opts.StatusMin > 0 && e.StatusCode < opts.StatusMin {
		return false
	}
	if opts.StatusMax > 0 && e.StatusCode > opts.StatusMax {
		return false
	}

	// Endpoint regex.
	if opts.EndpointRegex != nil && !opts.EndpointRegex.MatchString(e.Path) {
		return false
	}

	// IP whitelist.
	if len(opts.IPWhitelist) > 0 && !opts.IPWhitelist[e.RemoteAddr] {
		return false
	}

	// IP blacklist.
	if len(opts.IPBlacklist) > 0 && opts.IPBlacklist[e.RemoteAddr] {
		return false
	}

	return true
}
