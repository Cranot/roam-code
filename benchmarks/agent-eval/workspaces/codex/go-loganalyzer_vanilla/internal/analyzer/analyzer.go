package analyzer

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"go-loganalyzer/internal/parser"
)

type Options struct {
	Workers    int
	ProgressFn func(bytesRead int64)
	FileDoneFn func()
}

type fileResult struct {
	report FileReport
	acc    *metricsAccumulator
	err    error
}

func AnalyzeFiles(ctx context.Context, files []string, filters Filters, options Options) (Report, error) {
	workers := options.Workers
	if workers <= 0 {
		workers = runtime.NumCPU()
	}

	jobs := make(chan string)
	results := make(chan fileResult, len(files))

	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-ctx.Done():
					return
				case path, ok := <-jobs:
					if !ok {
						return
					}

					report, acc, err := analyzeFile(path, filters, options.ProgressFn)
					if options.FileDoneFn != nil {
						options.FileDoneFn()
					}
					select {
					case <-ctx.Done():
						return
					case results <- fileResult{report: report, acc: acc, err: err}:
					}
				}
			}
		}()
	}

	go func() {
		defer close(jobs)
		for _, path := range files {
			select {
			case <-ctx.Done():
				return
			case jobs <- path:
			}
		}
	}()

	go func() {
		wg.Wait()
		close(results)
	}()

	fileReports := make([]FileReport, 0, len(files))
	aggregate := newAccumulator()
	var errs []error

	for result := range results {
		if result.err != nil {
			errs = append(errs, result.err)
			continue
		}
		fileReports = append(fileReports, result.report)
		aggregate.merge(result.acc)
	}

	sort.Slice(fileReports, func(i, j int) bool { return fileReports[i].Path < fileReports[j].Path })

	report := Report{
		AnalyzedAt: time.Now().UTC(),
		FileCount:  len(fileReports),
		Files:      fileReports,
		Aggregate:  aggregate.snapshot(),
	}

	if ctx.Err() != nil {
		return report, ctx.Err()
	}
	if len(errs) == 0 {
		return report, nil
	}
	if len(fileReports) == 0 {
		return report, errors.Join(errs...)
	}
	return report, fmt.Errorf("completed with file errors: %w", errors.Join(errs...))
}

func AnalyzeFile(path string, filters Filters) (FileReport, error) {
	report, _, err := analyzeFile(path, filters, nil)
	return report, err
}

func analyzeFile(path string, filters Filters, progressFn func(int64)) (FileReport, *metricsAccumulator, error) {
	file, err := os.Open(path)
	if err != nil {
		return FileReport{}, nil, fmt.Errorf("%s: %w", path, err)
	}
	defer file.Close()

	reader := bufio.NewReader(file)
	var sampleLines []string
	var bufferedLines []string

	for len(sampleLines) < 10 {
		line, err := reader.ReadString('\n')
		if line != "" {
			if progressFn != nil {
				progressFn(int64(len(line)))
			}
			trimmed := strings.TrimRight(line, "\r\n")
			bufferedLines = append(bufferedLines, trimmed)
			if strings.TrimSpace(trimmed) != "" {
				sampleLines = append(sampleLines, trimmed)
			}
		}

		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return FileReport{}, nil, fmt.Errorf("%s: %w", path, err)
		}
	}

	format := parser.DetectFormat(sampleLines)
	lineParser := parser.NewLineParser(format)
	acc := newAccumulator()

	processLine := func(line string) {
		if strings.TrimSpace(line) == "" {
			return
		}
		entry, err := lineParser.ParseLine(line)
		if err != nil {
			acc.markSkippedLine()
			return
		}
		if !filters.Match(entry) {
			return
		}
		acc.addEntry(entry)
	}

	for _, line := range bufferedLines {
		processLine(line)
	}

	for {
		line, err := reader.ReadString('\n')
		if line != "" {
			if progressFn != nil {
				progressFn(int64(len(line)))
			}
			processLine(strings.TrimRight(line, "\r\n"))
		}

		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return FileReport{}, nil, fmt.Errorf("%s: %w", path, err)
		}
	}

	return FileReport{
		Path:    path,
		Format:  string(format),
		Metrics: acc.snapshot(),
	}, acc, nil
}

func DiscoverFiles(inputs []string) ([]string, error) {
	files := make([]string, 0)
	seen := map[string]struct{}{}

	for _, input := range inputs {
		info, err := os.Stat(input)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", input, err)
		}

		if !info.IsDir() {
			if _, ok := seen[input]; !ok {
				files = append(files, input)
				seen[input] = struct{}{}
			}
			continue
		}

		err = filepath.WalkDir(input, func(path string, d fs.DirEntry, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			if d.IsDir() {
				return nil
			}
			if _, ok := seen[path]; ok {
				return nil
			}
			files = append(files, path)
			seen[path] = struct{}{}
			return nil
		})
		if err != nil {
			return nil, err
		}
	}

	sort.Strings(files)
	return files, nil
}

func TotalFileSize(paths []string) int64 {
	var total int64
	for _, path := range paths {
		info, err := os.Stat(path)
		if err != nil || info.IsDir() {
			continue
		}
		total += info.Size()
	}
	return total
}
