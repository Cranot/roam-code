package parser

import "testing"

const (
	apacheLogLine = `127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /index.html HTTP/1.0" 200 2326 "http://example.com" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"`
	nginxLogLine  = `10.0.0.1 - - [15/Nov/2023:12:00:00 +0000] "GET /api/users HTTP/1.1" 200 512 "http://example.com/home" "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" 0.123`
	jsonLogLine   = `{"timestamp":"2023-11-15T12:00:00Z","ip":"192.168.1.100","method":"GET","path":"/api/test","status":200,"size":1024,"response_time":45.3,"user_agent":"curl/7.68.0"}`
)

func BenchmarkApacheParser(b *testing.B) {
	parser := NewApacheParser()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		_, _ = parser.Parse(apacheLogLine)
	}
}

func BenchmarkNginxParser(b *testing.B) {
	parser := NewNginxParser()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		_, _ = parser.Parse(nginxLogLine)
	}
}

func BenchmarkJSONParser(b *testing.B) {
	parser := NewJSONParser()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		_, _ = parser.Parse(jsonLogLine)
	}
}

func BenchmarkApacheDetect(b *testing.B) {
	parser := NewApacheParser()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		_ = parser.Detect(apacheLogLine)
	}
}

func BenchmarkNginxDetect(b *testing.B) {
	parser := NewNginxParser()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		_ = parser.Detect(nginxLogLine)
	}
}

func BenchmarkJSONDetect(b *testing.B) {
	parser := NewJSONParser()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		_ = parser.Detect(jsonLogLine)
	}
}

func BenchmarkParseTimestamp(b *testing.B) {
	timestamps := []string{
		"2023-11-15T12:00:00Z",
		"10/Oct/2000:13:55:36 -0700",
		"2023-11-15T12:00:00.123456789Z",
	}

	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		ts := timestamps[i%len(timestamps)]
		_, _ = parseTimestamp(ts)
	}
}
