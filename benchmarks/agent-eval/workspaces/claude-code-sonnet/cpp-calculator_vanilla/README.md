# Mathematical Expression Parser and Calculator

A feature-rich mathematical expression parser and interactive calculator written in C++ with a clean architecture separating lexing, parsing, and evaluation.

## Features

### Mathematical Operations
- **Basic operators**: `+`, `-`, `*`, `/`, `%` (modulo), `^` (power)
- **Correct operator precedence**: follows standard mathematical order
- **Parentheses**: arbitrary nesting depth for grouping expressions
- **Unary operators**: `-5`, `-(3+2)`, `+7`

### Built-in Functions
- **Trigonometric**: `sin`, `cos`, `tan`
- **Logarithmic**: `log` (natural), `log10` (base 10)
- **Mathematical**: `sqrt`, `abs`, `ceil`, `floor`
- **Aggregation**: `min`, `max` (accept multiple arguments)

### Constants
- `pi` - π (3.14159...)
- `e` - Euler's number (2.71828...)

### Variables
- Assign values: `x = 3.14`
- Use in expressions: `x * 2 + 5`
- Variables persist throughout session

### History
- Access previous results: `$1`, `$2`, `$3`, etc.
- Results are numbered sequentially starting from 1
- Useful for building on previous calculations

### Error Handling
- Clear error messages with position information
- Examples:
  - `"Unexpected token '*' at position 5"`
  - `"Unknown function 'foo'"`
  - `"Division by zero"`

### Modes
- **Interactive REPL**: Type expressions and see results immediately
- **File evaluation**: Read expressions from a file, output results

## Build Instructions

### Prerequisites
- CMake 3.16 or higher
- C++17 compatible compiler (GCC, Clang, MSVC)
- Internet connection (for downloading Catch2 test framework)

### Building

```bash
# Create build directory
mkdir build
cd build

# Configure
cmake ..

# Build
cmake --build .

# Run tests
ctest --output-on-failure
# Or run the test executable directly
./calculator_test
```

### Build Targets
- `calculator` - Main calculator executable
- `calculator_lib` - Core library (lexer, parser, evaluator)
- `calculator_test` - Unit tests (Catch2)

## Usage

### Interactive REPL Mode

Run without arguments to start the interactive calculator:

```bash
./calculator
```

Example session:
```
calc> 2 + 3 * 4
14

calc> x = 5
5

calc> sqrt(x^2 + 3^2)
5.830951894845301

calc> sin(pi / 4)
0.707106781186547

calc> $1 * 2
11.661903789690602

calc> min(1, 2, 3) + max(4, 5, 6)
7

calc> help
[shows help information]

calc> quit
```

### File Evaluation Mode

Create a text file with expressions (one per line):

```bash
# example.txt
x = 10
y = 20
x + y
sqrt(x^2 + y^2)
sin(pi/6)
```

Run with file argument:

```bash
./calculator example.txt
```

Output:
```
Line 1: x = 10
  Result: 10
Line 2: y = 20
  Result: 20
Line 3: x + y
  Result: 30
Line 4: sqrt(x^2 + y^2)
  Result: 22.360679774997898
Line 5: sin(pi/6)
  Result: 0.5
```

### REPL Commands

- `help` - Display help information
- `quit` or `exit` - Exit the calculator

## Architecture

The calculator follows a clean separation of concerns:

```
┌─────────────┐
│   Input     │
│   String    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Lexer     │ ← Tokenizes input into tokens
│  (Token.h)  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Parser    │ ← Builds Abstract Syntax Tree (AST)
│  (Parser.h) │   using recursive descent
└──────┬──────┘
       │
       ▼
┌─────────────┐
│     AST     │ ← Tree representation of expression
│   (AST.h)   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Evaluator  │ ← Evaluates AST, manages variables
│(Evaluator.h)│   functions, and history
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Result    │
└─────────────┘
```

### Key Components

#### Token & Lexer (`Token.h`, `Lexer.h`)
- Converts input string into sequence of tokens
- Identifies numbers, operators, identifiers, parentheses
- Tracks position for error reporting

#### AST Nodes (`AST.h`)
- `NumberNode` - Literal numeric values
- `BinaryOpNode` - Binary operations (+, -, *, /, %, ^)
- `UnaryOpNode` - Unary operations (+, -)
- `VariableNode` - Variable references
- `FunctionCallNode` - Function calls with arguments
- `AssignmentNode` - Variable assignments

#### Parser (`Parser.h`)
- Implements recursive descent parsing
- Respects operator precedence:
  1. Parentheses (highest)
  2. Unary +/-
  3. Power (^) - right associative
  4. Multiplication, Division, Modulo
  5. Addition, Subtraction (lowest)
- Builds AST from token stream

#### Evaluator (`Evaluator.h`)
- Traverses AST to compute results
- Manages variable storage
- Implements built-in functions
- Maintains calculation history
- Provides constants (pi, e)

## Examples

### Basic Arithmetic
```
calc> 2 + 3
5

calc> (2 + 3) * 4
20

calc> 2 ^ 3 ^ 2
512
```

### Functions
```
calc> sqrt(16)
4

calc> sin(pi/2)
1

calc> min(10, 5, 8, 3)
3

calc> max(1, 2, 3, 4, 5)
5
```

### Variables
```
calc> radius = 5
5

calc> area = pi * radius^2
78.53981633974483

calc> circumference = 2 * pi * radius
31.41592653589793
```

### History
```
calc> 10 + 5
15

calc> 20 * 3
60

calc> $1 + $2
75
```

### Complex Expressions
```
calc> a = 1
1

calc> b = -5
-5

calc> c = 6
6

calc> x1 = (-b + sqrt(b^2 - 4*a*c)) / (2*a)
3

calc> x2 = (-b - sqrt(b^2 - 4*a*c)) / (2*a)
2
```

## Testing

The project includes comprehensive unit tests using Catch2:

```bash
# Run all tests
cd build
ctest --output-on-failure

# Or run test executable with Catch2 options
./calculator_test

# Run specific test
./calculator_test "[evaluator]"

# Verbose output
./calculator_test -s
```

Test coverage includes:
- Lexer tokenization
- Parser correctness and error handling
- All operators and precedence rules
- All built-in functions
- Variable assignment and retrieval
- History mechanism
- Error conditions (division by zero, unknown variables, etc.)
- Complex nested expressions

## Project Structure

```
cpp-calculator/
├── CMakeLists.txt       # Build configuration
├── README.md            # This file
├── include/             # Header files
│   ├── Token.h          # Token types and Token class
│   ├── Lexer.h          # Lexer/tokenizer
│   ├── AST.h            # Abstract Syntax Tree nodes
│   ├── Parser.h         # Recursive descent parser
│   └── Evaluator.h      # Expression evaluator
├── src/                 # Implementation files
│   ├── Token.cpp
│   ├── Lexer.cpp
│   ├── AST.cpp
│   ├── Parser.cpp
│   ├── Evaluator.cpp
│   └── main.cpp         # REPL and file mode
└── test/                # Unit tests
    └── test_main.cpp    # Catch2 tests
```

## Implementation Details

### Parsing Algorithm
The parser uses **recursive descent** with the following grammar:

```
expression    → assignment
assignment    → IDENTIFIER '=' additive | additive
additive      → multiplicative (('+' | '-') multiplicative)*
multiplicative→ power (('*' | '/' | '%') power)*
power         → unary ('^' power)?      # Right-associative
unary         → ('+' | '-') unary | primary
primary       → NUMBER
              | IDENTIFIER '(' arguments ')'  # Function call
              | IDENTIFIER                     # Variable
              | '(' expression ')'
arguments     → (expression (',' expression)*)?
```

### Right Associativity of Power
The power operator is right-associative, meaning `2^3^4` is evaluated as `2^(3^4) = 2^81`, not `(2^3)^4 = 8^4`.

### History Implementation
Results are stored in a vector with 1-based indexing for user convenience. History references ($1, $2, etc.) are resolved during variable lookup.

## License

This project is provided as-is for educational and practical use.
