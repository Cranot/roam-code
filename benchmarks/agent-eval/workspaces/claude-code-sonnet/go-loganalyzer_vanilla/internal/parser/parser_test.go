package parser

import (
	"testing"
)

func TestApacheParser(t *testing.T) {
	tests := []struct {
		name      string
		line      string
		wantError bool
		wantIP    string
		wantCode  int
	}{
		{
			name:      "valid apache log",
			line:      `127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /index.html HTTP/1.0" 200 2326 "http://example.com" "Mozilla/5.0"`,
			wantError: false,
			wantIP:    "127.0.0.1",
			wantCode:  200,
		},
		{
			name:      "apache log with dash size",
			line:      `192.168.1.1 - - [10/Oct/2000:13:55:36 -0700] "POST /api/data HTTP/1.1" 404 - "http://example.com" "curl/7.64.0"`,
			wantError: false,
			wantIP:    "192.168.1.1",
			wantCode:  404,
		},
		{
			name:      "invalid format",
			line:      `invalid log line`,
			wantError: true,
		},
	}

	parser := NewApacheParser()

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry, err := parser.Parse(tt.line)

			if tt.wantError {
				if err == nil {
					t.Errorf("expected error, got nil")
				}
				return
			}

			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			if entry.IP != tt.wantIP {
				t.Errorf("IP = %v, want %v", entry.IP, tt.wantIP)
			}

			if entry.StatusCode != tt.wantCode {
				t.Errorf("StatusCode = %v, want %v", entry.StatusCode, tt.wantCode)
			}
		})
	}
}

func TestNginxParser(t *testing.T) {
	tests := []struct {
		name         string
		line         string
		wantError    bool
		wantIP       string
		wantCode     int
		wantRespTime float64
	}{
		{
			name:         "nginx log with response time",
			line:         `10.0.0.1 - - [15/Nov/2023:12:00:00 +0000] "GET /api/users HTTP/1.1" 200 512 "-" "Mozilla/5.0" 0.123`,
			wantError:    false,
			wantIP:       "10.0.0.1",
			wantCode:     200,
			wantRespTime: 123.0, // Converted to ms
		},
		{
			name:         "nginx log without response time",
			line:         `10.0.0.2 - - [15/Nov/2023:12:00:00 +0000] "POST /login HTTP/1.1" 401 256 "-" "curl/7.68.0"`,
			wantError:    false,
			wantIP:       "10.0.0.2",
			wantCode:     401,
			wantRespTime: 0,
		},
	}

	parser := NewNginxParser()

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry, err := parser.Parse(tt.line)

			if tt.wantError {
				if err == nil {
					t.Errorf("expected error, got nil")
				}
				return
			}

			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			if entry.IP != tt.wantIP {
				t.Errorf("IP = %v, want %v", entry.IP, tt.wantIP)
			}

			if entry.StatusCode != tt.wantCode {
				t.Errorf("StatusCode = %v, want %v", entry.StatusCode, tt.wantCode)
			}

			if entry.ResponseTime != tt.wantRespTime {
				t.Errorf("ResponseTime = %v, want %v", entry.ResponseTime, tt.wantRespTime)
			}
		})
	}
}

func TestJSONParser(t *testing.T) {
	tests := []struct {
		name      string
		line      string
		wantError bool
		wantIP    string
		wantCode  int
	}{
		{
			name:      "valid JSON log",
			line:      `{"timestamp":"2023-11-15T12:00:00Z","ip":"192.168.1.100","method":"GET","path":"/api/test","status":200,"size":1024}`,
			wantError: false,
			wantIP:    "192.168.1.100",
			wantCode:  200,
		},
		{
			name:      "JSON with alternative field names",
			line:      `{"time":"2023-11-15T12:00:00Z","remote_addr":"10.0.0.1","request_method":"POST","uri":"/submit","status_code":201}`,
			wantError: false,
			wantIP:    "10.0.0.1",
			wantCode:  201,
		},
		{
			name:      "invalid JSON",
			line:      `{invalid json`,
			wantError: true,
		},
	}

	parser := NewJSONParser()

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			entry, err := parser.Parse(tt.line)

			if tt.wantError {
				if err == nil {
					t.Errorf("expected error, got nil")
				}
				return
			}

			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}

			if entry.IP != tt.wantIP {
				t.Errorf("IP = %v, want %v", entry.IP, tt.wantIP)
			}

			if entry.StatusCode != tt.wantCode {
				t.Errorf("StatusCode = %v, want %v", entry.StatusCode, tt.wantCode)
			}
		})
	}
}

func TestParseTimestamp(t *testing.T) {
	tests := []struct {
		name      string
		input     string
		wantError bool
	}{
		{
			name:      "RFC3339",
			input:     "2023-11-15T12:00:00Z",
			wantError: false,
		},
		{
			name:      "RFC3339Nano",
			input:     "2023-11-15T12:00:00.123456789Z",
			wantError: false,
		},
		{
			name:      "Apache format",
			input:     "10/Oct/2000:13:55:36 -0700",
			wantError: false,
		},
		{
			name:      "invalid format",
			input:     "not a timestamp",
			wantError: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := parseTimestamp(tt.input)

			if tt.wantError && err == nil {
				t.Errorf("expected error, got nil")
			}

			if !tt.wantError && err != nil {
				t.Errorf("unexpected error: %v", err)
			}
		})
	}
}

func TestDetection(t *testing.T) {
	tests := []struct {
		name       string
		line       string
		wantFormat LogFormat
	}{
		{
			name:       "detect apache",
			line:       `127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /index.html HTTP/1.0" 200 2326 "http://example.com" "Mozilla/5.0"`,
			wantFormat: FormatApacheCombined,
		},
		{
			name:       "detect nginx",
			line:       `10.0.0.1 - - [15/Nov/2023:12:00:00 +0000] "GET /api/users HTTP/1.1" 200 512 "-" "Mozilla/5.0" 0.123`,
			wantFormat: FormatNginx,
		},
		{
			name:       "detect JSON",
			line:       `{"timestamp":"2023-11-15T12:00:00Z","ip":"192.168.1.100","method":"GET","path":"/api/test","status":200}`,
			wantFormat: FormatJSONLines,
		},
	}

	parsers := []struct {
		parser Parser
		format LogFormat
	}{
		{NewApacheParser(), FormatApacheCombined},
		{NewNginxParser(), FormatNginx},
		{NewJSONParser(), FormatJSONLines},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			detected := false
			for _, p := range parsers {
				if p.parser.Detect(tt.line) && p.format == tt.wantFormat {
					detected = true
					break
				}
			}

			if !detected {
				t.Errorf("failed to detect format %v", tt.wantFormat)
			}
		})
	}
}
