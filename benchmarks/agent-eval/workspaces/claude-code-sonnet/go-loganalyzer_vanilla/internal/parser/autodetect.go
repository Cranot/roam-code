package parser

import (
	"bufio"
	"fmt"
	"io"
)

// AutoDetectFormat tries to detect the log format from the first valid line
func AutoDetectFormat(reader io.Reader) (Parser, LogFormat, error) {
	parsers := []struct {
		parser Parser
		format LogFormat
	}{
		{NewApacheParser(), FormatApacheCombined},
		{NewNginxParser(), FormatNginx},
		{NewJSONParser(), FormatJSONLines},
	}

	scanner := bufio.NewScanner(reader)
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}

		for _, p := range parsers {
			if p.parser.Detect(line) {
				return p.parser, p.format, nil
			}
		}
	}

	if err := scanner.Err(); err != nil {
		return nil, FormatUnknown, fmt.Errorf("error reading input: %w", err)
	}

	return nil, FormatUnknown, fmt.Errorf("unable to detect log format")
}

// ParseLine tries to parse a line with the given parser
func ParseLine(parser Parser, line string) (*LogEntry, error) {
	if line == "" {
		return nil, fmt.Errorf("empty line")
	}
	return parser.Parse(line)
}
