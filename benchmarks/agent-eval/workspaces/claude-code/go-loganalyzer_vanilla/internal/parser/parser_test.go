package parser

import (
	"strings"
	"testing"
	"time"
)

func TestParseLine(t *testing.T) {
	tests := []struct {
		name       string
		line       string
		wantFormat Format
		wantErr    bool
		check      func(t *testing.T, e LogEntry)
	}{
		{
			name:       "apache combined basic",
			line:       `127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326 "http://www.example.com/start.html" "Mozilla/4.08"`,
			wantFormat: FormatApacheCombined,
			check: func(t *testing.T, e LogEntry) {
				if e.RemoteAddr != "127.0.0.1" {
					t.Errorf("RemoteAddr = %q, want %q", e.RemoteAddr, "127.0.0.1")
				}
				if e.Method != "GET" {
					t.Errorf("Method = %q, want %q", e.Method, "GET")
				}
				if e.Path != "/apache_pb.gif" {
					t.Errorf("Path = %q, want %q", e.Path, "/apache_pb.gif")
				}
				if e.StatusCode != 200 {
					t.Errorf("StatusCode = %d, want %d", e.StatusCode, 200)
				}
				if e.BodyBytes != 2326 {
					t.Errorf("BodyBytes = %d, want %d", e.BodyBytes, 2326)
				}
				if e.UserAgent != "Mozilla/4.08" {
					t.Errorf("UserAgent = %q, want %q", e.UserAgent, "Mozilla/4.08")
				}
				wantTime := time.Date(2000, 10, 10, 13, 55, 36, 0, time.FixedZone("", -7*3600))
				if !e.Timestamp.Equal(wantTime) {
					t.Errorf("Timestamp = %v, want %v", e.Timestamp, wantTime)
				}
			},
		},
		{
			name:       "apache combined with response time",
			line:       `10.0.0.1 - - [15/Mar/2024:08:30:00 +0000] "POST /api/users HTTP/1.1" 201 512 "-" "curl/7.88.1" 0.234`,
			wantFormat: FormatApacheCombined,
			check: func(t *testing.T, e LogEntry) {
				if e.RemoteAddr != "10.0.0.1" {
					t.Errorf("RemoteAddr = %q, want %q", e.RemoteAddr, "10.0.0.1")
				}
				if e.Method != "POST" {
					t.Errorf("Method = %q, want %q", e.Method, "POST")
				}
				if e.StatusCode != 201 {
					t.Errorf("StatusCode = %d, want %d", e.StatusCode, 201)
				}
				if e.ResponseTime != 0.234 {
					t.Errorf("ResponseTime = %f, want %f", e.ResponseTime, 0.234)
				}
			},
		},
		{
			name:       "apache combined dash body bytes",
			line:       `192.168.1.1 - - [01/Jan/2024:00:00:00 +0000] "GET /health HTTP/1.1" 304 - "-" "HealthChecker/1.0"`,
			wantFormat: FormatApacheCombined,
			check: func(t *testing.T, e LogEntry) {
				if e.StatusCode != 304 {
					t.Errorf("StatusCode = %d, want %d", e.StatusCode, 304)
				}
				if e.BodyBytes != 0 {
					t.Errorf("BodyBytes = %d, want %d", e.BodyBytes, 0)
				}
			},
		},
		{
			name:       "json lines basic",
			line:       `{"remote_addr":"10.0.0.5","method":"GET","path":"/api/v1/status","status":200,"body_bytes_sent":128,"timestamp":"2024-03-15T10:30:00Z","response_time":0.045}`,
			wantFormat: FormatJSONLines,
			check: func(t *testing.T, e LogEntry) {
				if e.RemoteAddr != "10.0.0.5" {
					t.Errorf("RemoteAddr = %q, want %q", e.RemoteAddr, "10.0.0.5")
				}
				if e.Method != "GET" {
					t.Errorf("Method = %q, want %q", e.Method, "GET")
				}
				if e.Path != "/api/v1/status" {
					t.Errorf("Path = %q, want %q", e.Path, "/api/v1/status")
				}
				if e.StatusCode != 200 {
					t.Errorf("StatusCode = %d, want %d", e.StatusCode, 200)
				}
				if e.ResponseTime != 0.045 {
					t.Errorf("ResponseTime = %f, want %f", e.ResponseTime, 0.045)
				}
			},
		},
		{
			name:       "json lines with request field",
			line:       `{"ip":"10.0.0.6","request":"GET /index.html HTTP/1.1","status_code":200,"size":4096,"time":"2024-06-01 12:00:00","user_agent":"TestAgent"}`,
			wantFormat: FormatJSONLines,
			check: func(t *testing.T, e LogEntry) {
				if e.RemoteAddr != "10.0.0.6" {
					t.Errorf("RemoteAddr = %q, want %q", e.RemoteAddr, "10.0.0.6")
				}
				if e.Method != "GET" {
					t.Errorf("Method = %q, want %q", e.Method, "GET")
				}
				if e.Path != "/index.html" {
					t.Errorf("Path = %q, want %q", e.Path, "/index.html")
				}
				if e.Protocol != "HTTP/1.1" {
					t.Errorf("Protocol = %q, want %q", e.Protocol, "HTTP/1.1")
				}
				if e.StatusCode != 200 {
					t.Errorf("StatusCode = %d, want %d", e.StatusCode, 200)
				}
				if e.BodyBytes != 4096 {
					t.Errorf("BodyBytes = %d, want %d", e.BodyBytes, 4096)
				}
			},
		},
		{
			name:    "empty line",
			line:    "",
			wantErr: true,
		},
		{
			name:    "whitespace only",
			line:    "   \t  ",
			wantErr: true,
		},
		{
			name:    "garbage",
			line:    "this is not a log line at all",
			wantErr: true,
		},
		{
			name:    "invalid json",
			line:    `{"broken json`,
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry, format, err := ParseLine(tt.line, 1, "test.log")
			if tt.wantErr {
				if err == nil {
					t.Fatal("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if format != tt.wantFormat {
				t.Errorf("format = %v, want %v", format, tt.wantFormat)
			}
			if tt.check != nil {
				tt.check(t, entry)
			}
		})
	}
}

func TestParseReader(t *testing.T) {
	tests := []struct {
		name         string
		input        string
		wantEntries  int
		wantSkipped  int
		wantTotal    int
		wantFormat   Format
	}{
		{
			name: "mixed apache lines with blanks and garbage",
			input: `127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /page1 HTTP/1.0" 200 100 "-" "Agent"

this is garbage
192.168.1.1 - - [10/Oct/2000:14:00:00 -0700] "POST /api HTTP/1.1" 500 50 "-" "Agent"
`,
			wantEntries: 2,
			wantSkipped: 2,
			wantTotal:   4,
			wantFormat:  FormatApacheCombined,
		},
		{
			name: "json lines",
			input: `{"remote_addr":"10.0.0.1","method":"GET","path":"/a","status":200}
{"remote_addr":"10.0.0.2","method":"POST","path":"/b","status":404}
{"remote_addr":"10.0.0.3","method":"DELETE","path":"/c","status":500}
`,
			wantEntries: 3,
			wantSkipped: 0,
			wantTotal:   3,
			wantFormat:  FormatJSONLines,
		},
		{
			name:         "empty input",
			input:        "",
			wantEntries:  0,
			wantSkipped:  0,
			wantTotal:    0,
			wantFormat:   FormatUnknown,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := strings.NewReader(tt.input)
			result := ParseReader(r, "test.log")

			if len(result.Entries) != tt.wantEntries {
				t.Errorf("entries = %d, want %d", len(result.Entries), tt.wantEntries)
			}
			if result.SkippedLines != tt.wantSkipped {
				t.Errorf("skipped = %d, want %d", result.SkippedLines, tt.wantSkipped)
			}
			if result.TotalLines != tt.wantTotal {
				t.Errorf("total = %d, want %d", result.TotalLines, tt.wantTotal)
			}
			if result.Format != tt.wantFormat {
				t.Errorf("format = %v, want %v", result.Format, tt.wantFormat)
			}
		})
	}
}

func TestFormatString(t *testing.T) {
	tests := []struct {
		f    Format
		want string
	}{
		{FormatUnknown, "unknown"},
		{FormatApacheCombined, "apache_combined"},
		{FormatNginx, "nginx"},
		{FormatJSONLines, "json_lines"},
	}

	for _, tt := range tests {
		t.Run(tt.want, func(t *testing.T) {
			if got := tt.f.String(); got != tt.want {
				t.Errorf("String() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestParseTimestamp(t *testing.T) {
	tests := []struct {
		input string
		want  time.Time
	}{
		{"2024-03-15T10:30:00Z", time.Date(2024, 3, 15, 10, 30, 0, 0, time.UTC)},
		{"2024-03-15T10:30:00", time.Date(2024, 3, 15, 10, 30, 0, 0, time.UTC)},
		{"2024-03-15 10:30:00", time.Date(2024, 3, 15, 10, 30, 0, 0, time.UTC)},
		{"not-a-timestamp", time.Time{}},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := parseTimestamp(tt.input)
			if !got.Equal(tt.want) {
				t.Errorf("parseTimestamp(%q) = %v, want %v", tt.input, got, tt.want)
			}
		})
	}
}

func TestCoalesceStr(t *testing.T) {
	tests := []struct {
		args []string
		want string
	}{
		{[]string{"", "", "c"}, "c"},
		{[]string{"a", "b"}, "a"},
		{[]string{"", ""}, ""},
		{[]string{"x"}, "x"},
	}

	for _, tt := range tests {
		got := coalesceStr(tt.args...)
		if got != tt.want {
			t.Errorf("coalesceStr(%v) = %q, want %q", tt.args, got, tt.want)
		}
	}
}
