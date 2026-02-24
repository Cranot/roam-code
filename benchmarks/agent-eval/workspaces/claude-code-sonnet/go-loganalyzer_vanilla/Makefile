.PHONY: build test bench clean run install

# Build the binary
build:
	go build -o loganalyzer cmd/loganalyzer/main.go

# Build for multiple platforms
build-all:
	GOOS=linux GOARCH=amd64 go build -o loganalyzer-linux-amd64 cmd/loganalyzer/main.go
	GOOS=darwin GOARCH=amd64 go build -o loganalyzer-darwin-amd64 cmd/loganalyzer/main.go
	GOOS=windows GOARCH=amd64 go build -o loganalyzer-windows-amd64.exe cmd/loganalyzer/main.go

# Run tests
test:
	go test -v ./...

# Run tests with coverage
test-coverage:
	go test -cover ./...
	go test -coverprofile=coverage.out ./...
	go tool cover -html=coverage.out

# Run benchmarks
bench:
	go test -bench=. -benchmem ./internal/parser/

# Run with race detector
test-race:
	go test -race ./...

# Clean build artifacts
clean:
	rm -f loganalyzer loganalyzer-* coverage.out

# Install to $GOPATH/bin
install:
	go install ./cmd/loganalyzer

# Format code
fmt:
	go fmt ./...

# Run linter
lint:
	golangci-lint run

# Run example
run-example: build
	./loganalyzer test_logs/
