cmake_minimum_required(VERSION 3.16)

project(cpp_calculator VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(calculator
    src/ast.cpp
    src/context.cpp
    src/engine.cpp
    src/evaluator.cpp
    src/lexer.cpp
    src/parser.cpp
    src/repl.cpp
    src/token.cpp
    src/value.cpp
)

target_include_directories(calculator
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

add_executable(calc src/main.cpp)
target_link_libraries(calc PRIVATE calculator)

if(MINGW)
    target_link_options(calc PRIVATE -static -static-libgcc -static-libstdc++)
endif()

include(CTest)
if(BUILD_TESTING)
    include(FetchContent)
    FetchContent_Declare(
        Catch2
        GIT_REPOSITORY https://github.com/catchorg/Catch2.git
        GIT_TAG v3.5.4
    )
    FetchContent_MakeAvailable(Catch2)

    add_executable(calculator_tests tests/test_calculator.cpp)
    target_link_libraries(calculator_tests PRIVATE calculator Catch2::Catch2WithMain)
    if(MINGW)
        target_link_options(calculator_tests PRIVATE -static -static-libgcc -static-libstdc++)
    endif()

    add_test(NAME calculator_tests COMMAND calculator_tests)
endif()
