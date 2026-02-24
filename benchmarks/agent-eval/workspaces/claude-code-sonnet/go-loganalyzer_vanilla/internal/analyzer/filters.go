package analyzer

import (
	"regexp"
	"time"

	"github.com/example/loganalyzer/internal/parser"
)

// Filters holds filtering criteria
type Filters struct {
	StartDate      *time.Time
	EndDate        *time.Time
	MinStatus      int
	MaxStatus      int
	EndpointRegex  *regexp.Regexp
	IPWhitelist    map[string]bool
	IPBlacklist    map[string]bool
}

// NewFilters creates a new Filters instance
func NewFilters() *Filters {
	return &Filters{
		IPWhitelist: make(map[string]bool),
		IPBlacklist: make(map[string]bool),
	}
}

// Matches returns true if the entry passes all filters
func (f *Filters) Matches(entry *parser.LogEntry) bool {
	// Date range filter
	if f.StartDate != nil && !entry.Timestamp.IsZero() {
		if entry.Timestamp.Before(*f.StartDate) {
			return false
		}
	}

	if f.EndDate != nil && !entry.Timestamp.IsZero() {
		if entry.Timestamp.After(*f.EndDate) {
			return false
		}
	}

	// Status code range filter
	if f.MinStatus > 0 && entry.StatusCode < f.MinStatus {
		return false
	}

	if f.MaxStatus > 0 && entry.StatusCode > f.MaxStatus {
		return false
	}

	// Endpoint regex filter
	if f.EndpointRegex != nil {
		if !f.EndpointRegex.MatchString(entry.Endpoint) {
			return false
		}
	}

	// IP whitelist filter (if set, only allow whitelisted IPs)
	if len(f.IPWhitelist) > 0 {
		if !f.IPWhitelist[entry.IP] {
			return false
		}
	}

	// IP blacklist filter (if set, reject blacklisted IPs)
	if len(f.IPBlacklist) > 0 {
		if f.IPBlacklist[entry.IP] {
			return false
		}
	}

	return true
}

// SetDateRange sets the date range filter
func (f *Filters) SetDateRange(start, end *time.Time) {
	f.StartDate = start
	f.EndDate = end
}

// SetStatusRange sets the status code range filter
func (f *Filters) SetStatusRange(min, max int) {
	f.MinStatus = min
	f.MaxStatus = max
}

// SetEndpointPattern sets the endpoint regex filter
func (f *Filters) SetEndpointPattern(pattern string) error {
	if pattern == "" {
		f.EndpointRegex = nil
		return nil
	}

	regex, err := regexp.Compile(pattern)
	if err != nil {
		return err
	}

	f.EndpointRegex = regex
	return nil
}

// AddIPWhitelist adds IPs to the whitelist
func (f *Filters) AddIPWhitelist(ips []string) {
	for _, ip := range ips {
		f.IPWhitelist[ip] = true
	}
}

// AddIPBlacklist adds IPs to the blacklist
func (f *Filters) AddIPBlacklist(ips []string) {
	for _, ip := range ips {
		f.IPBlacklist[ip] = true
	}
}
