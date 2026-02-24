package analyzer

import (
	"sort"
	"sync"
	"time"

	"github.com/example/loganalyzer/internal/parser"
)

// Statistics holds analysis results
type Statistics struct {
	mu sync.Mutex

	TotalRequests  int
	UniqueIPs      map[string]int
	UniqueEndpoints map[string]int
	StatusCodes    map[int]int

	IPRequests       map[string]int
	EndpointRequests map[string]int
	SlowRequests     []*parser.LogEntry

	HourlyRequests map[string]int // hour -> count
	HourlyErrors   map[string]int // hour -> error count

	Status2xx int
	Status3xx int
	Status4xx int
	Status5xx int
}

// NewStatistics creates a new Statistics instance
func NewStatistics() *Statistics {
	return &Statistics{
		UniqueIPs:        make(map[string]int),
		UniqueEndpoints:  make(map[string]int),
		StatusCodes:      make(map[int]int),
		IPRequests:       make(map[string]int),
		EndpointRequests: make(map[string]int),
		SlowRequests:     make([]*parser.LogEntry, 0),
		HourlyRequests:   make(map[string]int),
		HourlyErrors:     make(map[string]int),
	}
}

// AddEntry adds a log entry to statistics
func (s *Statistics) AddEntry(entry *parser.LogEntry) {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.TotalRequests++

	// Track unique IPs
	s.UniqueIPs[entry.IP]++
	s.IPRequests[entry.IP]++

	// Track unique endpoints
	s.UniqueEndpoints[entry.Endpoint]++
	s.EndpointRequests[entry.Endpoint]++

	// Track status codes
	s.StatusCodes[entry.StatusCode]++

	// Categorize by status code range
	if entry.StatusCode >= 200 && entry.StatusCode < 300 {
		s.Status2xx++
	} else if entry.StatusCode >= 300 && entry.StatusCode < 400 {
		s.Status3xx++
	} else if entry.StatusCode >= 400 && entry.StatusCode < 500 {
		s.Status4xx++
	} else if entry.StatusCode >= 500 {
		s.Status5xx++
	}

	// Track slow requests (top 10)
	if entry.ResponseTime > 0 {
		s.SlowRequests = append(s.SlowRequests, entry)
		// Keep only top 10 slowest
		if len(s.SlowRequests) > 10 {
			sort.Slice(s.SlowRequests, func(i, j int) bool {
				return s.SlowRequests[i].ResponseTime > s.SlowRequests[j].ResponseTime
			})
			s.SlowRequests = s.SlowRequests[:10]
		}
	}

	// Track hourly requests
	if !entry.Timestamp.IsZero() {
		hour := entry.Timestamp.Format("2006-01-02 15:00")
		s.HourlyRequests[hour]++

		// Track errors (4xx and 5xx)
		if entry.StatusCode >= 400 {
			s.HourlyErrors[hour]++
		}
	}
}

// Merge merges another Statistics into this one
func (s *Statistics) Merge(other *Statistics) {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.TotalRequests += other.TotalRequests

	// Merge unique IPs
	for ip, count := range other.UniqueIPs {
		s.UniqueIPs[ip] += count
	}

	// Merge unique endpoints
	for endpoint, count := range other.UniqueEndpoints {
		s.UniqueEndpoints[endpoint] += count
	}

	// Merge status codes
	for code, count := range other.StatusCodes {
		s.StatusCodes[code] += count
	}

	// Merge IP requests
	for ip, count := range other.IPRequests {
		s.IPRequests[ip] += count
	}

	// Merge endpoint requests
	for endpoint, count := range other.EndpointRequests {
		s.EndpointRequests[endpoint] += count
	}

	// Merge status counts
	s.Status2xx += other.Status2xx
	s.Status3xx += other.Status3xx
	s.Status4xx += other.Status4xx
	s.Status5xx += other.Status5xx

	// Merge slow requests
	s.SlowRequests = append(s.SlowRequests, other.SlowRequests...)
	if len(s.SlowRequests) > 10 {
		sort.Slice(s.SlowRequests, func(i, j int) bool {
			return s.SlowRequests[i].ResponseTime > s.SlowRequests[j].ResponseTime
		})
		s.SlowRequests = s.SlowRequests[:10]
	}

	// Merge hourly requests
	for hour, count := range other.HourlyRequests {
		s.HourlyRequests[hour] += count
	}

	// Merge hourly errors
	for hour, count := range other.HourlyErrors {
		s.HourlyErrors[hour] += count
	}
}

// TopIPs returns the top N IPs by request count
func (s *Statistics) TopIPs(n int) []KVPair {
	return topN(s.IPRequests, n)
}

// TopEndpoints returns the top N endpoints by request count
func (s *Statistics) TopEndpoints(n int) []KVPair {
	return topN(s.EndpointRequests, n)
}

// KVPair represents a key-value pair for sorting
type KVPair struct {
	Key   string
	Value int
}

func topN(m map[string]int, n int) []KVPair {
	pairs := make([]KVPair, 0, len(m))
	for k, v := range m {
		pairs = append(pairs, KVPair{k, v})
	}

	sort.Slice(pairs, func(i, j int) bool {
		return pairs[i].Value > pairs[j].Value
	})

	if len(pairs) > n {
		pairs = pairs[:n]
	}

	return pairs
}

// ErrorRateSpikes detects hours with abnormally high error rates
func (s *Statistics) ErrorRateSpikes(threshold float64) []ErrorSpike {
	spikes := make([]ErrorSpike, 0)

	// Calculate average error rate
	totalErrors := 0
	totalRequests := 0
	for hour := range s.HourlyRequests {
		totalRequests += s.HourlyRequests[hour]
		totalErrors += s.HourlyErrors[hour]
	}

	if totalRequests == 0 {
		return spikes
	}

	avgErrorRate := float64(totalErrors) / float64(totalRequests)

	// Find spikes
	for hour := range s.HourlyRequests {
		requests := s.HourlyRequests[hour]
		errors := s.HourlyErrors[hour]

		if requests == 0 {
			continue
		}

		errorRate := float64(errors) / float64(requests)

		// Spike if error rate is significantly higher than average
		if errorRate > avgErrorRate*threshold {
			spikes = append(spikes, ErrorSpike{
				Hour:      hour,
				Requests:  requests,
				Errors:    errors,
				ErrorRate: errorRate,
			})
		}
	}

	// Sort by time
	sort.Slice(spikes, func(i, j int) bool {
		return spikes[i].Hour < spikes[j].Hour
	})

	return spikes
}

// ErrorSpike represents an hour with abnormally high error rate
type ErrorSpike struct {
	Hour      string
	Requests  int
	Errors    int
	ErrorRate float64
}

// HourlyHistogram returns hourly request counts sorted by time
func (s *Statistics) HourlyHistogram() []HourlyBucket {
	buckets := make([]HourlyBucket, 0, len(s.HourlyRequests))

	for hour, count := range s.HourlyRequests {
		timestamp, _ := time.Parse("2006-01-02 15:00", hour)
		buckets = append(buckets, HourlyBucket{
			Hour:      hour,
			Timestamp: timestamp,
			Requests:  count,
		})
	}

	sort.Slice(buckets, func(i, j int) bool {
		return buckets[i].Timestamp.Before(buckets[j].Timestamp)
	})

	return buckets
}

// HourlyBucket represents requests in a one-hour window
type HourlyBucket struct {
	Hour      string
	Timestamp time.Time
	Requests  int
}
