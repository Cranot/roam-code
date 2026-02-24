# Complete Feature List

## Mathematical Operators

| Operator | Description | Precedence | Associativity | Example |
|----------|-------------|------------|---------------|---------|
| `( )` | Parentheses | Highest | N/A | `(2 + 3) * 4` |
| `+` `-` | Unary plus/minus | 2 | Right | `-5`, `+(3)` |
| `^` | Power | 3 | Right | `2^3^2 = 512` |
| `*` `/` `%` | Multiply, divide, modulo | 4 | Left | `6 / 3 * 2` |
| `+` `-` | Addition, subtraction | 5 (Lowest) | Left | `2 + 3 - 1` |

## Built-in Functions

### Trigonometric Functions
| Function | Description | Domain | Example |
|----------|-------------|--------|---------|
| `sin(x)` | Sine | All reals | `sin(pi/2) = 1` |
| `cos(x)` | Cosine | All reals | `cos(0) = 1` |
| `tan(x)` | Tangent | All reals except π/2 + nπ | `tan(0) = 0` |

### Logarithmic Functions
| Function | Description | Domain | Example |
|----------|-------------|--------|---------|
| `log(x)` | Natural logarithm | x > 0 | `log(e) = 1` |
| `log10(x)` | Base-10 logarithm | x > 0 | `log10(100) = 2` |

### Mathematical Functions
| Function | Description | Domain | Example |
|----------|-------------|--------|---------|
| `sqrt(x)` | Square root | x ≥ 0 | `sqrt(16) = 4` |
| `abs(x)` | Absolute value | All reals | `abs(-5) = 5` |
| `ceil(x)` | Ceiling | All reals | `ceil(3.2) = 4` |
| `floor(x)` | Floor | All reals | `floor(3.8) = 3` |

### Aggregation Functions
| Function | Description | Arguments | Example |
|----------|-------------|-----------|---------|
| `min(...)` | Minimum value | 1 or more | `min(1,2,3) = 1` |
| `max(...)` | Maximum value | 1 or more | `max(1,2,3) = 3` |

## Built-in Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `pi` | 3.141592653589793 | π (pi) |
| `e` | 2.718281828459045 | Euler's number |

## Variable System

### Assignment
```
variable_name = expression
```
- Variables persist throughout the session
- Can be reassigned
- Can reference other variables

### Examples
```
x = 5
y = x * 2
z = sqrt(x^2 + y^2)
```

## History System

### Access Previous Results
```
$1    # First result
$2    # Second result
$n    # Nth result
```

### Example Session
```
calc> 10 + 5
15                  ← This becomes $1

calc> 20 * 3
60                  ← This becomes $2

calc> $1 + $2
75                  ← This becomes $3

calc> $3 / 3
25                  ← This becomes $4
```

## Expression Types

### 1. Arithmetic Expressions
```
2 + 3 * 4
(10 - 5) / 2
17 % 5
2^8
```

### 2. Function Calls
```
sin(pi/4)
sqrt(16)
max(1, 2, 3, 4, 5)
log10(100)
```

### 3. Variable Assignments
```
x = 10
radius = 5
area = pi * radius^2
```

### 4. Mixed Expressions
```
result = sqrt(x^2 + y^2) * 2
angle = sin(pi/6) + cos(pi/3)
value = max($1, $2, $3)
```

## Error Types & Messages

### Parse Errors
| Error | Example Input | Message |
|-------|---------------|---------|
| Unexpected token | `2 + + 3` | `Parse error: Unexpected token '+' at position N` |
| Missing operand | `2 +` | `Parse error: Expected token type...` |
| Unmatched parentheses | `(2 + 3` | `Parse error: Expected token type RPAREN` |
| Invalid character | `2 @ 3` | `Error: Unexpected token '@' at position 2` |

### Evaluation Errors
| Error | Example Input | Message |
|-------|---------------|---------|
| Division by zero | `1 / 0` | `Evaluation error: Division by zero` |
| Unknown variable | `foo + 5` | `Evaluation error: Unknown variable: foo` |
| Unknown function | `bar(5)` | `Evaluation error: Unknown function: bar` |
| Wrong argument count | `sin(1, 2)` | `Evaluation error: sin() expects 1 argument, got 2` |
| Domain error | `sqrt(-1)` | `Evaluation error: sqrt() of negative number` |
| Invalid history | `$99` | `Evaluation error: History index out of range` |

## REPL Commands

| Command | Description |
|---------|-------------|
| `help` | Display help information |
| `quit` | Exit calculator |
| `exit` | Exit calculator (alias for quit) |

## File Mode Features

### Comment Support
Lines starting with `#` are treated as comments and ignored.

```
# This is a comment
x = 10    # This works
# y = 20  # This entire line is ignored
```

### Blank Lines
Empty lines are automatically skipped.

### Output Format
```
Line N: <expression>
  Result: <value>
```

## Number Formats Supported

| Format | Example | Value |
|--------|---------|-------|
| Integer | `42` | 42.0 |
| Decimal | `3.14` | 3.14 |
| Leading decimal | `.5` | 0.5 |
| Scientific (via operators) | `1.5 * 10^6` | 1500000 |

## Special Capabilities

### Arbitrary Nesting
```
(((1 + 2) * (3 + 4)) ^ ((5 - 6) + (7 * 8)))
```

### Multiple Unary Operators
```
--5     # Double negative = 5
-+5     # Negative positive = -5
+-5     # Positive negative = -5
```

### Chained Operations
```
1 + 2 + 3 + 4 + 5
10 - 5 - 2 - 1
2 * 3 * 4 * 5
```

### Complex Formulas

#### Quadratic Formula
```
a = 1
b = -5
c = 6
x1 = (-b + sqrt(b^2 - 4*a*c)) / (2*a)
x2 = (-b - sqrt(b^2 - 4*a*c)) / (2*a)
```

#### Distance Formula
```
x1 = 0
y1 = 0
x2 = 3
y2 = 4
distance = sqrt((x2-x1)^2 + (y2-y1)^2)
```

#### Circle Properties
```
radius = 5
circumference = 2 * pi * radius
area = pi * radius^2
```

## Precision

- Internal precision: IEEE 754 double-precision (53-bit mantissa)
- Display precision: 15 significant digits
- Suitable for most scientific and engineering calculations

## Limitations

1. **No complex numbers**: Only real number arithmetic
2. **No symbolic math**: All operations are numeric
3. **No user-defined functions**: Only built-in functions available
4. **No multi-statement lines**: One expression per line
5. **No vector/matrix operations**: Scalar values only
6. **ASCII only**: No Unicode in identifiers

## Platform Support

- **Operating Systems**: Windows, Linux, macOS
- **Compilers**: GCC 7+, Clang 6+, MSVC 2017+
- **Architectures**: x86, x64, ARM (any supporting C++17)
- **Build Systems**: CMake 3.16+, Make, manual compilation

## Performance Characteristics

- **Lexing**: O(n) where n is input length
- **Parsing**: O(n) for well-formed expressions
- **Evaluation**: O(n) where n is number of AST nodes
- **Variable lookup**: O(log m) where m is number of variables
- **History access**: O(1)

**Typical performance**: Microsecond-level evaluation for simple expressions

## Memory Usage

- **Small footprint**: ~1-2 MB compiled binary
- **Runtime memory**: Minimal - grows with number of variables and history
- **No memory leaks**: Smart pointers ensure proper cleanup
- **Stack-based recursion**: Limited by expression complexity
