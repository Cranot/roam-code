package parser

import (
	"testing"
	"time"
)

func TestDetectFormat(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		lines  []string
		expect Format
	}{
		{
			name: "apache",
			lines: []string{
				`127.0.0.1 - - [10/Oct/2025:13:55:36 -0700] "GET /health HTTP/1.1" 200 2326 "-" "curl/8.0.0"`,
			},
			expect: FormatApache,
		},
		{
			name: "nginx",
			lines: []string{
				`127.0.0.1 - - [12/Dec/2025:19:06:24 +0000] "GET /api/v1/items HTTP/1.1" 500 612 "-" "curl/7.68.0" request_time=0.245`,
			},
			expect: FormatNginx,
		},
		{
			name: "jsonl",
			lines: []string{
				`{"timestamp":"2025-10-10T10:00:00Z","ip":"10.0.0.1","endpoint":"/v1/ping","status":200}`,
			},
			expect: FormatJSONL,
		},
		{
			name:   "unknown",
			lines:  []string{"hello world"},
			expect: FormatUnknown,
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := DetectFormat(tt.lines)
			if got != tt.expect {
				t.Fatalf("DetectFormat()=%s, want %s", got, tt.expect)
			}
		})
	}
}

func TestLineParsers(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name             string
		format           Format
		line             string
		expectIP         string
		expectEndpoint   string
		expectStatus     int
		expectHasLatency bool
	}{
		{
			name:             "apache line",
			format:           FormatApache,
			line:             `127.0.0.1 - - [10/Oct/2025:13:55:36 -0700] "GET /health HTTP/1.1" 200 2326 "-" "curl/8.0.0"`,
			expectIP:         "127.0.0.1",
			expectEndpoint:   "/health",
			expectStatus:     200,
			expectHasLatency: false,
		},
		{
			name:             "nginx line with request time",
			format:           FormatNginx,
			line:             `127.0.0.1 - - [12/Dec/2025:19:06:24 +0000] "GET /api/v1/items HTTP/1.1" 500 612 "-" "curl/7.68.0" request_time=0.245`,
			expectIP:         "127.0.0.1",
			expectEndpoint:   "/api/v1/items",
			expectStatus:     500,
			expectHasLatency: true,
		},
		{
			name:             "jsonl line",
			format:           FormatJSONL,
			line:             `{"timestamp":"2025-10-10T10:00:00Z","ip":"10.0.0.1","endpoint":"/v1/ping","status":201,"response_time_ms":12.2}`,
			expectIP:         "10.0.0.1",
			expectEndpoint:   "/v1/ping",
			expectStatus:     201,
			expectHasLatency: true,
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			parser := NewLineParser(tt.format)
			entry, err := parser.ParseLine(tt.line)
			if err != nil {
				t.Fatalf("ParseLine() error = %v", err)
			}
			if entry.IP != tt.expectIP {
				t.Fatalf("IP=%q, want %q", entry.IP, tt.expectIP)
			}
			if entry.Endpoint != tt.expectEndpoint {
				t.Fatalf("Endpoint=%q, want %q", entry.Endpoint, tt.expectEndpoint)
			}
			if entry.Status != tt.expectStatus {
				t.Fatalf("Status=%d, want %d", entry.Status, tt.expectStatus)
			}
			if entry.HasResponseTime != tt.expectHasLatency {
				t.Fatalf("HasResponseTime=%t, want %t", entry.HasResponseTime, tt.expectHasLatency)
			}
			if entry.Timestamp.IsZero() {
				t.Fatalf("Timestamp should not be zero")
			}
		})
	}
}

func TestJSONTimestampUnix(t *testing.T) {
	t.Parallel()

	parser := NewLineParser(FormatJSONL)
	entry, err := parser.ParseLine(`{"timestamp":1736076000,"ip":"10.0.0.1","endpoint":"/v1/ping","status":200}`)
	if err != nil {
		t.Fatalf("ParseLine() error = %v", err)
	}

	expected := time.Unix(1736076000, 0)
	if !entry.Timestamp.Equal(expected) {
		t.Fatalf("Timestamp=%s, want %s", entry.Timestamp, expected)
	}
}

func TestMalformedLine(t *testing.T) {
	t.Parallel()

	parser := NewLineParser(FormatApache)
	if _, err := parser.ParseLine("not a valid line"); err == nil {
		t.Fatalf("expected parse error")
	}
}
