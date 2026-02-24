# C++ Calculator

A mathematical expression parser and interactive calculator built in C++17.

## Features

- **Arithmetic operators:** `+`, `-`, `*`, `/`, `%` (modulo), `^` (power) with correct precedence
- **Parentheses:** nested to arbitrary depth
- **Unary minus:** `-5`, `-(3+2)`
- **Built-in functions:** `sin`, `cos`, `tan`, `sqrt`, `log`, `log10`, `abs`, `ceil`, `floor`, `min`, `max`
- **Variables:** `x = 3.14`, then use `x` in later expressions
- **Constants:** `pi`, `e`
- **Expression history:** recall previous results with `$1`, `$2`, etc.
- **Scientific notation:** `1.5e10`, `2E-3`
- **Interactive REPL** with help, variable listing, and history
- **File evaluation mode:** read expressions from a file

## Building

Requires CMake 3.16+ and a C++17 compiler.

```bash
mkdir build && cd build
cmake ..
cmake --build .
```

To build in Release mode:

```bash
cmake -DCMAKE_BUILD_TYPE=Release ..
cmake --build .
```

## Running

### Interactive mode

```bash
./calculator
```

This starts the REPL. Type expressions and press Enter:

```
> 2 + 3
5
> x = pi / 2
1.5707963268
> sin(x)
1
> $1 + 10
15
> help
```

### Single expression

```bash
./calculator -e "sqrt(2) * 3"
```

### File mode

```bash
./calculator expressions.txt
```

Lines starting with `#` are treated as comments. Each expression is evaluated in order, with variables and history carrying over.

### REPL commands

| Command   | Description              |
|-----------|--------------------------|
| `help`    | Show usage information   |
| `vars`    | List all variables       |
| `history` | Show expression history  |
| `clear`   | Clear history            |
| `quit`    | Exit (also `exit`)       |

## Operator precedence (lowest to highest)

1. `=` (assignment, right-associative)
2. `+`, `-` (addition, subtraction)
3. `*`, `/`, `%` (multiplication, division, modulo)
4. `^` (power, right-associative)
5. Unary `-`, `+`
6. Function calls, parentheses

## Running tests

Tests use [doctest](https://github.com/doctest/doctest) (fetched automatically by CMake).

```bash
cd build
ctest --output-on-failure
```

Or run the test executable directly:

```bash
./tests/test_calculator
```

## Project structure

```
include/calculator/
    lexer.h        Tokenizer interface
    parser.h       AST node types and recursive descent parser
    evaluator.h    Expression evaluator with variables and history
    repl.h         Interactive REPL and file evaluation
src/
    lexer.cpp      Tokenizer implementation
    parser.cpp     Recursive descent parser
    evaluator.cpp  Tree-walking evaluator
    repl.cpp       REPL loop and file reader
    main.cpp       Entry point (CLI argument handling)
tests/
    test_calculator.cpp   Unit tests (doctest)
CMakeLists.txt            Build configuration
```
