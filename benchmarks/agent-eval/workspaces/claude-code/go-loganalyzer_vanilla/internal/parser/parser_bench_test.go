package parser

import (
	"fmt"
	"strings"
	"testing"
)

var benchApacheLine = `192.168.1.100 - admin [15/Mar/2024:14:30:22 +0000] "GET /api/v1/users?page=1&limit=50 HTTP/1.1" 200 8192 "https://example.com/dashboard" "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"`

var benchApacheWithRT = `10.0.0.50 - - [15/Mar/2024:14:30:22 +0000] "POST /api/v1/orders HTTP/1.1" 201 256 "https://example.com/checkout" "Mozilla/5.0" 0.543`

var benchJSONLine = `{"remote_addr":"10.0.0.50","method":"POST","path":"/api/v1/orders","protocol":"HTTP/1.1","status":201,"body_bytes_sent":256,"referer":"https://example.com/checkout","user_agent":"Mozilla/5.0","timestamp":"2024-03-15T14:30:22Z","response_time":0.543}`

func BenchmarkParseLineApache(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		ParseLine(benchApacheLine, i, "bench.log")
	}
}

func BenchmarkParseLineApacheWithRT(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		ParseLine(benchApacheWithRT, i, "bench.log")
	}
}

func BenchmarkParseLineJSON(b *testing.B) {
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		ParseLine(benchJSONLine, i, "bench.log")
	}
}

func BenchmarkParseReader100(b *testing.B) {
	benchmarkParseReaderN(b, 100)
}

func BenchmarkParseReader1000(b *testing.B) {
	benchmarkParseReaderN(b, 1000)
}

func BenchmarkParseReader10000(b *testing.B) {
	benchmarkParseReaderN(b, 10000)
}

func benchmarkParseReaderN(b *testing.B, n int) {
	var sb strings.Builder
	for i := 0; i < n; i++ {
		fmt.Fprintf(&sb, `192.168.1.%d - - [15/Mar/2024:%02d:30:22 +0000] "GET /page/%d HTTP/1.1" %d 1024 "-" "Agent"`,
			i%256, i%24, i, 200+((i%5)*100))
		sb.WriteByte('\n')
	}
	data := sb.String()

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		ParseReader(strings.NewReader(data), "bench.log")
	}
}

func BenchmarkParseReaderJSON1000(b *testing.B) {
	var sb strings.Builder
	for i := 0; i < 1000; i++ {
		fmt.Fprintf(&sb, `{"remote_addr":"10.0.0.%d","method":"GET","path":"/item/%d","status":%d,"body_bytes_sent":512,"timestamp":"2024-03-15T%02d:30:00Z","response_time":0.%03d}`,
			i%256, i, 200+((i%5)*100), i%24, i%1000)
		sb.WriteByte('\n')
	}
	data := sb.String()

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		ParseReader(strings.NewReader(data), "bench.log")
	}
}
