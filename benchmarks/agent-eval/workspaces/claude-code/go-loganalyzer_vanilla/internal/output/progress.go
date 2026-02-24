package output

import (
	"fmt"
	"io"
	"strings"
	"sync"
)

// ProgressBar renders a simple progress bar to a writer.
type ProgressBar struct {
	mu    sync.Mutex
	w     io.Writer
	width int
}

// NewProgressBar creates a new progress bar writing to w.
func NewProgressBar(w io.Writer) *ProgressBar {
	return &ProgressBar{w: w, width: 40}
}

// Update renders the progress bar at the given fraction.
func (pb *ProgressBar) Update(processed, total int, currentFile string) {
	pb.mu.Lock()
	defer pb.mu.Unlock()

	pct := float64(processed) / float64(total)
	filled := int(pct * float64(pb.width))
	if filled > pb.width {
		filled = pb.width
	}

	bar := strings.Repeat("=", filled) + strings.Repeat(" ", pb.width-filled)

	// Truncate filename for display.
	display := currentFile
	if len(display) > 40 {
		display = "..." + display[len(display)-37:]
	}

	fmt.Fprintf(pb.w, "\r  [%s] %3.0f%% (%d/%d) %s", bar, pct*100, processed, total, display)

	if processed == total {
		fmt.Fprintln(pb.w)
	}
}
