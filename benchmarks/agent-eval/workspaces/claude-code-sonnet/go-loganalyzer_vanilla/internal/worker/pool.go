package worker

import (
	"bufio"
	"fmt"
	"os"
	"sync"

	"github.com/example/loganalyzer/internal/analyzer"
	"github.com/example/loganalyzer/internal/parser"
)

// FileJob represents a file to be processed
type FileJob struct {
	Path   string
	Parser parser.Parser
}

// FileResult represents the result of processing a file
type FileResult struct {
	Path       string
	Stats      *analyzer.Statistics
	Skipped    int
	TotalLines int
	Error      error
}

// Pool manages concurrent file processing
type Pool struct {
	workers   int
	jobs      chan FileJob
	results   chan FileResult
	wg        sync.WaitGroup
	filters   *analyzer.Filters
	parser    parser.Parser
	autoDetect bool
}

// NewPool creates a new worker pool
func NewPool(workers int, filters *analyzer.Filters, p parser.Parser, autoDetect bool) *Pool {
	return &Pool{
		workers:    workers,
		jobs:       make(chan FileJob, workers*2),
		results:    make(chan FileResult, workers*2),
		filters:    filters,
		parser:     p,
		autoDetect: autoDetect,
	}
}

// Start starts the worker pool
func (p *Pool) Start() {
	for i := 0; i < p.workers; i++ {
		p.wg.Add(1)
		go p.worker()
	}
}

// Submit submits a file for processing
func (p *Pool) Submit(path string) {
	p.jobs <- FileJob{
		Path:   path,
		Parser: p.parser,
	}
}

// Close closes the job channel and waits for workers to finish
func (p *Pool) Close() {
	close(p.jobs)
	p.wg.Wait()
	close(p.results)
}

// Results returns the results channel
func (p *Pool) Results() <-chan FileResult {
	return p.results
}

func (p *Pool) worker() {
	defer p.wg.Done()

	for job := range p.jobs {
		result := p.processFile(job)
		p.results <- result
	}
}

func (p *Pool) processFile(job FileJob) FileResult {
	result := FileResult{
		Path: job.Path,
	}

	file, err := os.Open(job.Path)
	if err != nil {
		result.Error = fmt.Errorf("failed to open file: %w", err)
		return result
	}
	defer file.Close()

	// Auto-detect format if needed
	fileParser := job.Parser
	if p.autoDetect && fileParser == nil {
		// Read file once for detection
		detectedParser, _, err := parser.AutoDetectFormat(file)
		if err != nil {
			result.Error = fmt.Errorf("failed to detect format: %w", err)
			return result
		}
		fileParser = detectedParser

		// Reopen file for actual parsing
		file.Close()
		file, err = os.Open(job.Path)
		if err != nil {
			result.Error = fmt.Errorf("failed to reopen file: %w", err)
			return result
		}
	}

	if fileParser == nil {
		result.Error = fmt.Errorf("no parser available")
		return result
	}

	stats := analyzer.NewStatistics()
	scanner := bufio.NewScanner(file)

	// Increase buffer size for large lines
	buf := make([]byte, 0, 64*1024)
	scanner.Buffer(buf, 1024*1024)

	for scanner.Scan() {
		result.TotalLines++
		line := scanner.Text()

		if line == "" {
			continue
		}

		entry, err := fileParser.Parse(line)
		if err != nil {
			result.Skipped++
			continue
		}

		// Apply filters
		if p.filters != nil && !p.filters.Matches(entry) {
			continue
		}

		stats.AddEntry(entry)
	}

	if err := scanner.Err(); err != nil {
		result.Error = fmt.Errorf("error reading file: %w", err)
		return result
	}

	result.Stats = stats
	return result
}
