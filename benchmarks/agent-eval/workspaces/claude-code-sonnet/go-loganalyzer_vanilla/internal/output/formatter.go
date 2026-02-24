package output

import (
	"io"

	"github.com/example/loganalyzer/internal/analyzer"
)

// Formatter interface for different output formats
type Formatter interface {
	Format(stats *analyzer.Statistics, skipped, total int) error
}

// Format type
type Format string

const (
	FormatText Format = "text"
	FormatJSON Format = "json"
	FormatCSV  Format = "csv"
)

// NewFormatter creates a formatter based on the specified format
func NewFormatter(format Format, writer io.Writer) Formatter {
	switch format {
	case FormatJSON:
		return NewJSONFormatter(writer)
	case FormatCSV:
		return NewCSVFormatter(writer)
	default:
		return NewTextFormatter(writer)
	}
}
