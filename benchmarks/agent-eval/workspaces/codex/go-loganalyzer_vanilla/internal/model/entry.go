package model

import "time"

// Entry is a normalized log line representation used by analyzers.
type Entry struct {
	Timestamp       time.Time
	IP              string
	Endpoint        string
	Status          int
	ResponseTimeMs  float64
	HasResponseTime bool
}
