# C++ Expression Calculator

A mathematical expression parser and interactive calculator implemented in C++ with a recursive descent parser.

## Features

- Parse and evaluate expressions from string input
- Operators with precedence: `+`, `-`, `*`, `/`, `%`, `^`
- Parentheses with arbitrary nesting
- Unary minus: `-5`, `-(3+2)`
- Built-in functions:
  - `sin`, `cos`, `tan`, `sqrt`, `log`, `log10`, `abs`, `ceil`, `floor`, `min`, `max`
- Variable assignment and reuse: `x = 3.14`, then `x * 2`
- Built-in constants: `pi`, `e`
- Expression history references: `$1`, `$2`, ...
- Interactive REPL mode with `help`
- File evaluation mode
- Clear errors with token and position details
- Supports integer and floating-point arithmetic

## Project Structure

- `include/calculator/` - public headers
- `src/` - lexer, parser, evaluator, REPL, CLI
- `tests/` - doctest unit tests

## Build

Requirements:

- CMake 3.16+
- C++17 compiler

```bash
cmake -S . -B build
cmake --build build
```

## Run

Interactive REPL:

```bash
./build/calculator
```

File mode:

```bash
./build/calculator --file expressions.txt
```

Help:

```bash
./build/calculator --help
```

## REPL Commands

- `help` - show usage and examples
- `history` - list computed results (`$1`, `$2`, ...)
- `quit` / `exit` - leave REPL

## Example Expressions

```text
2 + 3 * 4
(2 + 3) * 4
x = 3.14
sin(pi / 2)
max($1, 10)
```

## Tests

```bash
ctest --test-dir build --output-on-failure
```