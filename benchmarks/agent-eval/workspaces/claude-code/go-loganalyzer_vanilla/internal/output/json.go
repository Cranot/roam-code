package output

import (
	"encoding/json"
	"io"

	"github.com/loganalyzer/internal/analyzer"
)

// JSONOutput is the top-level JSON structure.
type JSONOutput struct {
	Files     []analyzer.Stats `json:"files,omitempty"`
	Aggregate *analyzer.Stats  `json:"aggregate,omitempty"`
}

// WriteJSON writes stats as formatted JSON to w.
func WriteJSON(w io.Writer, fileStats []analyzer.Stats, aggregate *analyzer.Stats) error {
	out := JSONOutput{
		Files:     fileStats,
		Aggregate: aggregate,
	}
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(out)
}
