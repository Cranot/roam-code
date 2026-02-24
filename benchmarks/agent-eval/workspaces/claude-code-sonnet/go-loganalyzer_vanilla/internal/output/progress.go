package output

import (
	"fmt"
	"io"
	"sync"
	"time"
)

// ProgressBar displays progress for file processing
type ProgressBar struct {
	total     int
	current   int
	mu        sync.Mutex
	writer    io.Writer
	startTime time.Time
	lastPrint time.Time
	enabled   bool
}

// NewProgressBar creates a new progress bar
func NewProgressBar(total int, writer io.Writer, enabled bool) *ProgressBar {
	return &ProgressBar{
		total:     total,
		current:   0,
		writer:    writer,
		startTime: time.Now(),
		lastPrint: time.Now(),
		enabled:   enabled,
	}
}

// Increment increments the progress counter
func (p *ProgressBar) Increment() {
	if !p.enabled {
		return
	}

	p.mu.Lock()
	defer p.mu.Unlock()

	p.current++

	// Update display every 100ms or on completion
	now := time.Now()
	if now.Sub(p.lastPrint) > 100*time.Millisecond || p.current == p.total {
		p.print()
		p.lastPrint = now
	}
}

// Finish completes the progress bar
func (p *ProgressBar) Finish() {
	if !p.enabled {
		return
	}

	p.mu.Lock()
	defer p.mu.Unlock()

	p.current = p.total
	p.print()
	fmt.Fprintf(p.writer, "\n")
}

func (p *ProgressBar) print() {
	if p.total == 0 {
		return
	}

	percentage := float64(p.current) / float64(p.total) * 100
	elapsed := time.Since(p.startTime)

	// Estimate time remaining
	var eta time.Duration
	if p.current > 0 {
		avgTimePerItem := elapsed / time.Duration(p.current)
		remaining := p.total - p.current
		eta = avgTimePerItem * time.Duration(remaining)
	}

	// Create progress bar (40 chars wide)
	barWidth := 40
	filled := int(float64(barWidth) * float64(p.current) / float64(p.total))
	if filled > barWidth {
		filled = barWidth
	}

	bar := ""
	for i := 0; i < barWidth; i++ {
		if i < filled {
			bar += "="
		} else if i == filled {
			bar += ">"
		} else {
			bar += " "
		}
	}

	// Print progress bar
	fmt.Fprintf(p.writer, "\rProcessing: [%s] %d/%d (%.1f%%) ETA: %s",
		bar, p.current, p.total, percentage, formatDuration(eta))
}

func formatDuration(d time.Duration) string {
	if d < time.Second {
		return "0s"
	}

	d = d.Round(time.Second)
	h := d / time.Hour
	d -= h * time.Hour
	m := d / time.Minute
	d -= m * time.Minute
	s := d / time.Second

	if h > 0 {
		return fmt.Sprintf("%dh%dm%ds", h, m, s)
	}
	if m > 0 {
		return fmt.Sprintf("%dm%ds", m, s)
	}
	return fmt.Sprintf("%ds", s)
}
