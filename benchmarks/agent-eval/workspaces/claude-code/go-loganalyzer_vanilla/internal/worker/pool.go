package worker

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/loganalyzer/internal/analyzer"
	"github.com/loganalyzer/internal/filter"
	"github.com/loganalyzer/internal/parser"
)

// FileResult holds the result of processing a single file.
type FileResult struct {
	FilePath string
	Stats    analyzer.Stats
	Entries  []parser.LogEntry
	Err      error
}

// ProgressFunc is called after each file is processed.
// processed is the number of files done, total is the total number of files.
type ProgressFunc func(processed, total int, currentFile string)

// Pool manages concurrent file processing.
type Pool struct {
	Workers    int
	FilterOpts filter.Options
	OnProgress ProgressFunc
}

// DiscoverFiles finds all log files from the given paths.
// Paths can be files or directories (scanned recursively).
func DiscoverFiles(paths []string) ([]string, error) {
	var files []string
	seen := make(map[string]bool)

	for _, p := range paths {
		info, err := os.Stat(p)
		if err != nil {
			return nil, fmt.Errorf("cannot access %s: %w", p, err)
		}

		if !info.IsDir() {
			abs, _ := filepath.Abs(p)
			if !seen[abs] {
				seen[abs] = true
				files = append(files, p)
			}
			continue
		}

		err = filepath.Walk(p, func(path string, info os.FileInfo, err error) error {
			if err != nil {
				return nil
			}
			if info.IsDir() {
				return nil
			}
			if isLogFile(path) {
				abs, _ := filepath.Abs(path)
				if !seen[abs] {
					seen[abs] = true
					files = append(files, path)
				}
			}
			return nil
		})
		if err != nil {
			return nil, fmt.Errorf("error walking directory %s: %w", p, err)
		}
	}

	return files, nil
}

func isLogFile(path string) bool {
	ext := strings.ToLower(filepath.Ext(path))
	name := strings.ToLower(filepath.Base(path))
	switch ext {
	case ".log", ".jsonl", ".json", ".txt":
		return true
	}
	// Files named "access_log", "error_log", etc.
	if strings.Contains(name, "log") {
		return true
	}
	// Files with no extension that contain "access" or "log" in the name.
	if ext == "" && (strings.Contains(name, "access") || strings.Contains(name, "error")) {
		return true
	}
	return false
}

// Process processes all files concurrently using the worker pool.
func (p *Pool) Process(files []string) []FileResult {
	workers := p.Workers
	if workers < 1 {
		workers = 1
	}
	if workers > len(files) {
		workers = len(files)
	}

	results := make([]FileResult, len(files))
	fileCh := make(chan int, len(files))
	var processed int64
	total := len(files)

	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for idx := range fileCh {
				results[idx] = p.processFile(files[idx])
				done := int(atomic.AddInt64(&processed, 1))
				if p.OnProgress != nil {
					p.OnProgress(done, total, files[idx])
				}
			}
		}()
	}

	for i := range files {
		fileCh <- i
	}
	close(fileCh)
	wg.Wait()

	return results
}

func (p *Pool) processFile(path string) FileResult {
	f, err := os.Open(path)
	if err != nil {
		return FileResult{FilePath: path, Err: err}
	}
	defer f.Close()

	return p.processReader(f, path)
}

func (p *Pool) processReader(r io.Reader, path string) FileResult {
	result := parser.ParseReader(r, path)

	entries := filter.Apply(result.Entries, p.FilterOpts)

	stats := analyzer.Compute(entries)
	stats.SkippedLines = result.SkippedLines
	stats.TotalLines = result.TotalLines
	stats.Format = result.Format.String()
	stats.SourceFile = path

	return FileResult{
		FilePath: path,
		Stats:    stats,
		Entries:  entries,
	}
}
