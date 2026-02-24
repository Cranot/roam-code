# Architecture Documentation

## Overview

This calculator implements a complete mathematical expression evaluator using a classic compiler architecture:

```
Input String → Lexer → Parser → AST → Evaluator → Result
```

## Component Details

### 1. Token & Lexer (Tokenization)

**Files**: `Token.h`, `Token.cpp`, `Lexer.h`, `Lexer.cpp`

**Responsibility**: Convert raw input string into a sequence of typed tokens.

**Token Types**:
- `NUMBER`: Numeric literals (integers and floats)
- `IDENTIFIER`: Variable names and function names
- Operators: `PLUS`, `MINUS`, `MULTIPLY`, `DIVIDE`, `MODULO`, `POWER`
- Delimiters: `LPAREN`, `RPAREN`, `COMMA`
- Special: `ASSIGN`, `END`, `INVALID`

**Key Features**:
- Position tracking for error reporting
- Whitespace handling
- Support for identifiers with `$` prefix (history references)
- Decimal number parsing (including `.5` format)

**Example**:
```
Input: "x = 2 + 3"
Tokens: [IDENTIFIER("x"), ASSIGN("="), NUMBER("2"), PLUS("+"), NUMBER("3"), END]
```

### 2. AST (Abstract Syntax Tree)

**Files**: `AST.h`, `AST.cpp`

**Responsibility**: Represent the hierarchical structure of mathematical expressions.

**Node Types**:

1. **NumberNode**: Leaf node containing a numeric value
   ```cpp
   NumberNode(5.0)  // Represents the number 5.0
   ```

2. **BinaryOpNode**: Internal node with operator and two children
   ```cpp
   BinaryOpNode(left, '+', right)  // Represents: left + right
   ```

3. **UnaryOpNode**: Internal node with operator and one child
   ```cpp
   UnaryOpNode('-', operand)  // Represents: -operand
   ```

4. **VariableNode**: Leaf node referencing a variable
   ```cpp
   VariableNode("x")  // Represents the variable x
   ```

5. **FunctionCallNode**: Node with function name and argument list
   ```cpp
   FunctionCallNode("sin", [arg1])  // Represents: sin(arg1)
   ```

6. **AssignmentNode**: Assignment expression
   ```cpp
   AssignmentNode("x", expression)  // Represents: x = expression
   ```

**Example AST**:
```
Expression: "2 + 3 * 4"

      (+)
     /   \
   (2)   (*)
        /   \
      (3)   (4)
```

**Evaluation**: Each node implements `evaluate(Evaluator&)` which recursively evaluates children and combines results.

### 3. Parser (Syntax Analysis)

**Files**: `Parser.h`, `Parser.cpp`

**Responsibility**: Convert token stream into an Abstract Syntax Tree following grammar rules.

**Algorithm**: Recursive Descent Parsing

**Grammar** (in precedence order, lowest to highest):
```
expression    → assignment
assignment    → IDENTIFIER '=' additive | additive
additive      → multiplicative (('+' | '-') multiplicative)*
multiplicative→ power (('*' | '/' | '%') power)*
power         → unary ('^' power)?
unary         → ('+' | '-') unary | primary
primary       → NUMBER
              | IDENTIFIER '(' arguments ')'
              | IDENTIFIER
              | '(' expression ')'
arguments     → (expression (',' expression)*)?
```

**Key Design Decisions**:

1. **Left-recursive elimination**: Grammar avoids left recursion
2. **Right-associative power**: `2^3^4` = `2^(3^4)` implemented via recursion
3. **Operator precedence**: Encoded in parsing method call hierarchy
4. **Error handling**: Throws `ParseError` with position information

**Parse Tree Construction**:
```cpp
// Example: "2 + 3"
parse_expression()
  → parse_assignment()
    → parse_additive()
      → parse_multiplicative()
        → parse_power()
          → parse_unary()
            → parse_primary()
              → return NumberNode(2)
      → sees PLUS token
      → advance()
      → parse_multiplicative()
        → ... → return NumberNode(3)
      → return BinaryOpNode(2, '+', 3)
```

### 4. Evaluator (Execution Engine)

**Files**: `Evaluator.h`, `Evaluator.cpp`

**Responsibility**:
- Traverse AST and compute results
- Manage variable storage
- Implement built-in functions
- Maintain calculation history
- Provide mathematical constants

**State Management**:

1. **Variables**: `std::map<std::string, double>`
   - User-defined variables
   - Built-in constants (pi, e)

2. **History**: `std::vector<double>`
   - Stores previous results
   - 1-indexed for user convenience (`$1`, `$2`, etc.)

3. **Functions**: `std::map<std::string, std::function<...>>`
   - Built-in mathematical functions
   - Validation of argument counts

**Function Implementation Strategy**:

Functions are stored as lambdas with argument validation:
```cpp
functions["sqrt"] = [](const std::vector<double>& args) {
    if (args.size() != 1) {
        throw EvaluationError("sqrt() expects 1 argument");
    }
    if (args[0] < 0) {
        throw EvaluationError("sqrt() of negative number");
    }
    return std::sqrt(args[0]);
};
```

**Error Handling**:
- Division/modulo by zero
- Unknown variables/functions
- Invalid history references
- Domain errors (e.g., sqrt of negative, log of non-positive)

### 5. REPL (Read-Eval-Print Loop)

**Files**: `main.cpp`

**Responsibility**: Provide user interface for interactive and batch evaluation.

**Modes**:

1. **Interactive REPL**:
   ```
   calc> [user input]
   [result]
   ```
   - Persistent evaluator state
   - Command processing (help, quit)
   - Error handling and display

2. **File Evaluation**:
   - Read expressions from file (one per line)
   - Skip comments (lines starting with #)
   - Skip blank lines
   - Display line number and result

**Flow**:
```
┌─────────────────┐
│  Read Input     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Check Special  │ (quit, help)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Lexer.tokenize │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Parser.parse   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  AST.evaluate   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Add to history  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Print result   │
└─────────────────┘
```

## Error Handling Strategy

### 1. Lexical Errors
- Invalid characters → `INVALID` token
- Detected and reported during parsing

### 2. Syntax Errors
- Unexpected tokens → `ParseError` with position
- Missing closing parentheses
- Incomplete expressions

### 3. Runtime Errors
- Division by zero → `EvaluationError`
- Unknown variables/functions → `EvaluationError`
- Function argument errors → `EvaluationError`

**Error Message Format**:
```
Parse error: Unexpected token ')' at position 12
Evaluation error: Unknown variable: foo
Evaluation error: Division by zero
```

## Data Flow Example

**Input**: `"x = sqrt(9) + 2"`

1. **Lexer**:
   ```
   [IDENTIFIER("x"), ASSIGN("="), IDENTIFIER("sqrt"),
    LPAREN("("), NUMBER("9"), RPAREN(")"),
    PLUS("+"), NUMBER("2"), END]
   ```

2. **Parser**:
   ```
   AssignmentNode("x",
     BinaryOpNode(
       FunctionCallNode("sqrt", [NumberNode(9)]),
       '+',
       NumberNode(2)
     )
   )
   ```

3. **Evaluator**:
   ```
   evaluate AssignmentNode:
     evaluate BinaryOpNode:
       evaluate FunctionCallNode("sqrt"):
         evaluate NumberNode(9) → 9.0
         call sqrt([9.0]) → 3.0
       evaluate NumberNode(2) → 2.0
       compute 3.0 + 2.0 → 5.0
     set variable "x" = 5.0
     return 5.0
   ```

4. **Output**: `5.0`

## Memory Management

- **Smart Pointers**: All AST nodes use `std::unique_ptr` for automatic memory management
- **RAII**: Resources automatically cleaned up when objects go out of scope
- **No Manual Memory Management**: Zero `new`/`delete` in user code

## Extension Points

### Adding New Operators
1. Add token type to `TokenType` enum
2. Update lexer to recognize token
3. Add parsing rule at appropriate precedence level
4. Implement evaluation in `BinaryOpNode::evaluate()`

### Adding New Functions
1. Add lambda to `Evaluator::initialize_functions()`
2. Include argument validation
3. Call appropriate standard library function

### Adding New Constants
1. Add to `Evaluator::initialize_constants()`

## Testing Strategy

**File**: `test/test_main.cpp`

**Framework**: Catch2 v3

**Coverage**:
- Unit tests for each component (Lexer, Parser, Evaluator)
- Integration tests for complete expressions
- Error condition tests
- Edge cases (nested expressions, operator precedence, etc.)

**Test Categories**:
- `[lexer]` - Tokenization tests
- `[parser]` - Parsing tests
- `[evaluator]` - Evaluation tests

## Performance Considerations

1. **Single Pass Lexing**: Input scanned once
2. **Recursive Descent**: O(n) parsing for well-formed expressions
3. **Tree Evaluation**: O(n) where n is number of AST nodes
4. **Variable Lookup**: O(log n) using `std::map`
5. **History Access**: O(1) using `std::vector`

**Time Complexity**: O(n) where n is input length
**Space Complexity**: O(n) for AST storage

## Standards Compliance

- **C++ Standard**: C++17
- **Minimum CMake**: 3.16
- **External Dependencies**: Catch2 (testing only)
- **Standard Library**: Uses only standard library (no boost, etc.)

## Future Enhancement Ideas

1. **Complex numbers**: Support for imaginary numbers
2. **More functions**: Hyperbolic trig, gamma, etc.
3. **User-defined functions**: Allow function definitions
4. **Multiple statements**: Support semicolon-separated expressions
5. **Arrays/vectors**: Vector operations
6. **Symbolic math**: Algebraic simplification
7. **Plotting**: Integrate with graphing library
8. **Unit support**: Physical unit conversions
9. **Binary/hex**: Support different number bases
10. **Bitwise operators**: AND, OR, XOR, shift
