package worker

import (
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/loganalyzer/internal/filter"
)

func TestDiscoverFiles(t *testing.T) {
	// Create a temp directory structure.
	dir := t.TempDir()
	sub := filepath.Join(dir, "subdir")
	os.MkdirAll(sub, 0755)

	// Create test files.
	writeFile(t, filepath.Join(dir, "access.log"), apacheLine)
	writeFile(t, filepath.Join(dir, "data.txt"), "some data")
	writeFile(t, filepath.Join(sub, "app.log"), apacheLine)
	writeFile(t, filepath.Join(sub, "image.png"), "not a log")

	files, err := DiscoverFiles([]string{dir})
	if err != nil {
		t.Fatalf("DiscoverFiles error: %v", err)
	}

	// Should find access.log, data.txt (.txt extension matches), app.log.
	// image.png should be excluded.
	if len(files) < 3 {
		t.Errorf("found %d files, want >= 3", len(files))
	}

	for _, f := range files {
		if filepath.Ext(f) == ".png" {
			t.Errorf("should not include .png files, found %s", f)
		}
	}
}

func TestDiscoverFilesDirectFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "test.log")
	writeFile(t, path, apacheLine)

	files, err := DiscoverFiles([]string{path})
	if err != nil {
		t.Fatalf("DiscoverFiles error: %v", err)
	}
	if len(files) != 1 {
		t.Errorf("found %d files, want 1", len(files))
	}
}

func TestDiscoverFilesNonexistent(t *testing.T) {
	_, err := DiscoverFiles([]string{"/nonexistent/path"})
	if err == nil {
		t.Error("expected error for nonexistent path")
	}
}

func TestPoolProcess(t *testing.T) {
	dir := t.TempDir()

	// Create test log files.
	writeFile(t, filepath.Join(dir, "file1.log"), makeApacheLog(10))
	writeFile(t, filepath.Join(dir, "file2.log"), makeApacheLog(5))

	files, err := DiscoverFiles([]string{dir})
	if err != nil {
		t.Fatalf("DiscoverFiles error: %v", err)
	}

	pool := &Pool{
		Workers:    2,
		FilterOpts: filter.Options{},
	}

	results := pool.Process(files)
	if len(results) != len(files) {
		t.Fatalf("got %d results, want %d", len(results), len(files))
	}

	totalRequests := 0
	for _, r := range results {
		if r.Err != nil {
			t.Errorf("error processing %s: %v", r.FilePath, r.Err)
			continue
		}
		totalRequests += r.Stats.TotalRequests
	}

	if totalRequests != 15 {
		t.Errorf("total requests = %d, want 15", totalRequests)
	}
}

func TestPoolProgressCallback(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, "file1.log"), makeApacheLog(5))
	writeFile(t, filepath.Join(dir, "file2.log"), makeApacheLog(5))
	writeFile(t, filepath.Join(dir, "file3.log"), makeApacheLog(5))

	files, _ := DiscoverFiles([]string{dir})

	var callCount int64
	pool := &Pool{
		Workers:    2,
		FilterOpts: filter.Options{},
		OnProgress: func(processed, total int, currentFile string) {
			atomic.AddInt64(&callCount, 1)
		},
	}

	pool.Process(files)

	if atomic.LoadInt64(&callCount) != int64(len(files)) {
		t.Errorf("progress called %d times, want %d", callCount, len(files))
	}
}

func TestIsLogFile(t *testing.T) {
	tests := []struct {
		path string
		want bool
	}{
		{"access.log", true},
		{"error.log", true},
		{"data.jsonl", true},
		{"data.json", true},
		{"data.txt", true},
		{"access_log", true},
		{"image.png", false},
		{"binary.exe", false},
		{"style.css", false},
	}

	for _, tt := range tests {
		t.Run(tt.path, func(t *testing.T) {
			got := isLogFile(tt.path)
			if got != tt.want {
				t.Errorf("isLogFile(%q) = %v, want %v", tt.path, got, tt.want)
			}
		})
	}
}

const apacheLine = `127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /test HTTP/1.0" 200 1234 "-" "TestAgent"`

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatalf("failed to write %s: %v", path, err)
	}
}

func makeApacheLog(n int) string {
	lines := ""
	for i := 0; i < n; i++ {
		lines += `127.0.0.1 - user [15/Mar/2024:08:30:00 +0000] "GET /page HTTP/1.1" 200 512 "-" "TestAgent"` + "\n"
	}
	return lines
}
