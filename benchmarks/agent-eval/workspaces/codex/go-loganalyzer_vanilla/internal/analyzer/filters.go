package analyzer

import (
	"regexp"
	"strings"
	"time"

	"go-loganalyzer/internal/model"
)

type Filters struct {
	From          *time.Time
	To            *time.Time
	StatusMin     int
	StatusMax     int
	EndpointRegex *regexp.Regexp
	IPWhitelist   map[string]struct{}
	IPBlacklist   map[string]struct{}
}

func (f Filters) Match(entry model.Entry) bool {
	if f.From != nil {
		if entry.Timestamp.IsZero() || entry.Timestamp.Before(*f.From) {
			return false
		}
	}
	if f.To != nil {
		if entry.Timestamp.IsZero() || entry.Timestamp.After(*f.To) {
			return false
		}
	}
	if f.StatusMin > 0 && entry.Status < f.StatusMin {
		return false
	}
	if f.StatusMax > 0 && entry.Status > f.StatusMax {
		return false
	}
	if f.EndpointRegex != nil && !f.EndpointRegex.MatchString(entry.Endpoint) {
		return false
	}

	if len(f.IPWhitelist) > 0 {
		if _, ok := f.IPWhitelist[entry.IP]; !ok {
			return false
		}
	}
	if len(f.IPBlacklist) > 0 {
		if _, ok := f.IPBlacklist[entry.IP]; ok {
			return false
		}
	}
	return true
}

func ParseIPList(raw string) map[string]struct{} {
	out := map[string]struct{}{}
	if strings.TrimSpace(raw) == "" {
		return out
	}
	for _, piece := range strings.Split(raw, ",") {
		ip := strings.TrimSpace(piece)
		if ip == "" {
			continue
		}
		out[ip] = struct{}{}
	}
	return out
}
