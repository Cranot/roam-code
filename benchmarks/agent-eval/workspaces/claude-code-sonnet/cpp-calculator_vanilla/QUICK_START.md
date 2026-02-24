# Quick Start Guide

## Building

### Option 1: Using CMake (Recommended)
```bash
mkdir build && cd build
cmake ..
cmake --build .
./calculator
```

### Option 2: Using Make
```bash
make
./calculator
```

### Option 3: Using build script
```bash
chmod +x build.sh
./build.sh
```

### Option 4: Direct compilation
```bash
g++ -std=c++17 -Iinclude src/*.cpp -o calculator
./calculator
```

## Quick Examples

### Interactive Mode
```bash
./calculator
```

Then try:
```
calc> 2 + 3 * 4
14

calc> x = 5
5

calc> sqrt(x^2 + 3^2)
5.830951894845301

calc> $1 * 2
11.661903789690602

calc> quit
```

### File Mode
```bash
./calculator example.txt
```

## Cheat Sheet

### Operators (in order of precedence)
1. `( )` - Parentheses
2. `^` - Power (right-associative)
3. `+` `-` - Unary plus/minus
4. `*` `/` `%` - Multiply, divide, modulo
5. `+` `-` - Add, subtract

### Functions
| Function | Description | Example |
|----------|-------------|---------|
| `sin(x)` | Sine | `sin(pi/2)` → 1 |
| `cos(x)` | Cosine | `cos(0)` → 1 |
| `tan(x)` | Tangent | `tan(0)` → 0 |
| `sqrt(x)` | Square root | `sqrt(16)` → 4 |
| `log(x)` | Natural log | `log(e)` → 1 |
| `log10(x)` | Base-10 log | `log10(100)` → 2 |
| `abs(x)` | Absolute value | `abs(-5)` → 5 |
| `ceil(x)` | Ceiling | `ceil(3.2)` → 4 |
| `floor(x)` | Floor | `floor(3.8)` → 3 |
| `min(a,b,...)` | Minimum | `min(1,2,3)` → 1 |
| `max(a,b,...)` | Maximum | `max(1,2,3)` → 3 |

### Constants
- `pi` = 3.14159...
- `e` = 2.71828...

### Variables
```
x = 10          # Assign
y = x * 2       # Use in expression
```

### History
```
2 + 3           # First result
$1 * 2          # Use first result (2+3)*2 = 10
$2 + 1          # Use second result
```

## Common Tasks

### Quadratic Formula
```
a = 1
b = -5
c = 6
x1 = (-b + sqrt(b^2 - 4*a*c)) / (2*a)
x2 = (-b - sqrt(b^2 - 4*a*c)) / (2*a)
```

### Circle Calculations
```
r = 5
area = pi * r^2
circumference = 2 * pi * r
```

### Distance Formula
```
x1 = 0
y1 = 0
x2 = 3
y2 = 4
distance = sqrt((x2-x1)^2 + (y2-y1)^2)
```

## Testing
```bash
# CMake build
cd build
ctest --output-on-failure

# Or run test executable
./calculator_test
```

## Help
Type `help` in the REPL for built-in help.
See `README.md` for full documentation.
