#!/bin/bash
# Build script for the calculator project
# Works with both CMake and direct compilation

set -e  # Exit on error

echo "================================"
echo "Calculator Build Script"
echo "================================"
echo

# Check if CMake is available
if command -v cmake &> /dev/null; then
    echo "CMake found. Using CMake build..."
    mkdir -p build
    cd build
    cmake ..
    cmake --build .
    echo
    echo "Build complete! Executables in build/ directory"
    echo "Run: ./build/calculator"
    echo "Test: ctest --output-on-failure"
elif command -v g++ &> /dev/null; then
    echo "CMake not found. Using direct g++ compilation..."
    echo

    # Create output directory
    mkdir -p bin

    # Compile library files
    echo "Compiling library files..."
    g++ -std=c++17 -Wall -Iinclude -c src/Token.cpp -o bin/Token.o
    g++ -std=c++17 -Wall -Iinclude -c src/Lexer.cpp -o bin/Lexer.o
    g++ -std=c++17 -Wall -Iinclude -c src/AST.cpp -o bin/AST.o
    g++ -std=c++17 -Wall -Iinclude -c src/Parser.cpp -o bin/Parser.o
    g++ -std=c++17 -Wall -Iinclude -c src/Evaluator.cpp -o bin/Evaluator.o

    # Compile main
    echo "Compiling main..."
    g++ -std=c++17 -Wall -Iinclude -c src/main.cpp -o bin/main.o

    # Link
    echo "Linking..."
    g++ -std=c++17 bin/Token.o bin/Lexer.o bin/AST.o bin/Parser.o bin/Evaluator.o bin/main.o -o calculator

    echo
    echo "Build complete! Executable: ./calculator"
    echo "Run: ./calculator"
elif command -v cl &> /dev/null; then
    echo "CMake not found. Using MSVC compiler..."
    echo

    # Create output directory
    mkdir -p bin

    # Compile with MSVC
    cl /EHsc /std:c++17 /Iinclude /Fobin/ src/*.cpp /Fe:calculator.exe

    echo
    echo "Build complete! Executable: calculator.exe"
else
    echo "ERROR: No suitable compiler found!"
    echo "Please install one of: CMake + C++ compiler, g++, or MSVC"
    exit 1
fi

echo
echo "================================"
echo "Build successful!"
echo "================================"
