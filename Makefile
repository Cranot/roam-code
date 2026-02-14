.PHONY: install dev test lint format build publish clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest tests/

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

build:
	python -m build

publish: build
	twine upload dist/*

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info
