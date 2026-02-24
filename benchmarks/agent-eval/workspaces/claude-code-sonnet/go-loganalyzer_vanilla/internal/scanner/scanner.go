package scanner

import (
	"fmt"
	"os"
	"path/filepath"
)

// ScanPaths recursively scans directories and returns a list of log files
func ScanPaths(paths []string) ([]string, error) {
	var files []string
	seen := make(map[string]bool)

	for _, path := range paths {
		info, err := os.Stat(path)
		if err != nil {
			return nil, fmt.Errorf("failed to stat %s: %w", path, err)
		}

		if info.IsDir() {
			err := filepath.Walk(path, func(p string, info os.FileInfo, err error) error {
				if err != nil {
					return err
				}

				if info.IsDir() {
					return nil
				}

				// Only process files that look like logs
				if isLogFile(p) {
					absPath, err := filepath.Abs(p)
					if err != nil {
						return err
					}

					if !seen[absPath] {
						files = append(files, absPath)
						seen[absPath] = true
					}
				}

				return nil
			})

			if err != nil {
				return nil, fmt.Errorf("failed to walk directory %s: %w", path, err)
			}
		} else {
			// Single file
			absPath, err := filepath.Abs(path)
			if err != nil {
				return nil, fmt.Errorf("failed to get absolute path for %s: %w", path, err)
			}

			if !seen[absPath] {
				files = append(files, absPath)
				seen[absPath] = true
			}
		}
	}

	return files, nil
}

func isLogFile(path string) bool {
	ext := filepath.Ext(path)
	logExtensions := map[string]bool{
		".log":  true,
		".txt":  true,
		".json": true,
		"":      true, // Files without extension (common for logs)
	}

	// Check extension
	if logExtensions[ext] {
		return true
	}

	// Check filename patterns
	base := filepath.Base(path)
	if len(base) > 0 && (base[0] == '.' || base == "go.mod" || base == "go.sum") {
		return false
	}

	return true
}
