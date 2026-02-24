package parser

import "testing"

func BenchmarkParseApacheLine(b *testing.B) {
	line := `127.0.0.1 - - [10/Oct/2025:13:55:36 -0700] "GET /health HTTP/1.1" 200 2326 "-" "curl/8.0.0"`
	parser := NewLineParser(FormatApache)

	b.ReportAllocs()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		if _, err := parser.ParseLine(line); err != nil {
			b.Fatalf("ParseLine() error = %v", err)
		}
	}
}

func BenchmarkParseJSONLine(b *testing.B) {
	line := `{"timestamp":"2025-10-10T10:00:00Z","ip":"10.0.0.1","endpoint":"/v1/ping","status":201,"response_time_ms":12.2}`
	parser := NewLineParser(FormatJSONL)

	b.ReportAllocs()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		if _, err := parser.ParseLine(line); err != nil {
			b.Fatalf("ParseLine() error = %v", err)
		}
	}
}
