# C++ Expression Calculator

A C++ calculator with a recursive-descent parser, AST evaluator, REPL, and file mode.

## Features

- Parse and evaluate expressions from string input
- Operators with precedence and associativity: `+`, `-`, `*`, `/`, `%`, `^`
- Parentheses with arbitrary nesting
- Unary minus (examples: `-5`, `-(3+2)`)
- Built-in functions:
  - `sin`, `cos`, `tan`, `sqrt`, `log`, `log10`, `abs`, `ceil`, `floor`, `min`, `max`
- Variable assignment and reuse:
  - `x = 3.14`
  - `x * 2`
- Built-in constants: `pi`, `e`
- Result history references: `$1`, `$2`, ...
- Interactive REPL with `help`, `exit`, `quit`
- File evaluation mode
- Clear error messages with token and position where applicable
- Integer and floating-point arithmetic support

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
./build/calc
```

File mode:

```bash
./build/calc --file expressions.txt
# or
./build/calc expressions.txt
```

Help:

```bash
./build/calc --help
```

## Expressions

Examples:

- `1 + 2 * 3`
- `(1 + 2) * 3`
- `2 ^ 3 ^ 2`
- `sqrt(16) + max(3, 10, 8)`
- `x = 3.14`
- `sin(x)`
- `$1 + $2`

## Tests

```bash
ctest --test-dir build --output-on-failure
```

Tests are implemented with Catch2 and cover:

- precedence / associativity
- unary expressions
- functions and constants
- variables and history
- parser and evaluator errors