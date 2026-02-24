cmake_minimum_required(VERSION 3.16)
project(Calculator VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Include directories
include_directories(include)

# Library sources
set(LIB_SOURCES
    src/Token.cpp
    src/Lexer.cpp
    src/AST.cpp
    src/Parser.cpp
    src/Evaluator.cpp
)

# Create library
add_library(calculator_lib ${LIB_SOURCES})
target_include_directories(calculator_lib PUBLIC include)

# Main executable
add_executable(calculator src/main.cpp)
target_link_libraries(calculator calculator_lib)

# Enable testing
enable_testing()

# Download and configure Catch2
include(FetchContent)
FetchContent_Declare(
    Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.4.0
)
FetchContent_MakeAvailable(Catch2)

# Test executable
add_executable(calculator_test test/test_main.cpp)
target_link_libraries(calculator_test calculator_lib Catch2::Catch2WithMain)

# Register tests
add_test(NAME calculator_test COMMAND calculator_test)
