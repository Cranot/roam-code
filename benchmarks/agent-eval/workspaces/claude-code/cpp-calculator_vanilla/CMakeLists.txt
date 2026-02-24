cmake_minimum_required(VERSION 3.16)
project(calculator VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(MSVC)
    add_compile_definitions(_USE_MATH_DEFINES)
endif()

# Library with all calculator logic
add_library(calculator_lib
    src/lexer.cpp
    src/parser.cpp
    src/evaluator.cpp
    src/repl.cpp
)
target_include_directories(calculator_lib PUBLIC include)

# Main executable
add_executable(calculator src/main.cpp)
target_link_libraries(calculator PRIVATE calculator_lib)

# Testing
option(BUILD_TESTS "Build unit tests" ON)
if(BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()
