cmake_minimum_required(VERSION 3.16)

project(cpp_calculator LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(calculator_lib
  src/calculator.cpp
  src/context.cpp
  src/evaluator.cpp
  src/format.cpp
  src/lexer.cpp
  src/parser.cpp
  src/repl.cpp
)

target_include_directories(calculator_lib
  PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

add_executable(calculator src/main.cpp)
target_link_libraries(calculator PRIVATE calculator_lib)

if(MSVC)
  target_compile_options(calculator_lib PRIVATE /W4 /permissive-)
  target_compile_options(calculator PRIVATE /W4 /permissive-)
else()
  target_compile_options(calculator_lib PRIVATE -Wall -Wextra -Wpedantic)
  target_compile_options(calculator PRIVATE -Wall -Wextra -Wpedantic)
endif()

include(CTest)
if(BUILD_TESTING)
  include(FetchContent)
  FetchContent_Declare(
    doctest
    URL https://github.com/doctest/doctest/archive/refs/tags/v2.4.12.tar.gz
  )
  FetchContent_MakeAvailable(doctest)

  add_executable(calculator_tests tests/test_calculator.cpp)
  target_link_libraries(calculator_tests PRIVATE calculator_lib doctest::doctest)
  if(WIN32 AND MINGW)
    get_filename_component(MINGW_BIN_DIR "${CMAKE_CXX_COMPILER}" DIRECTORY)
    add_test(
      NAME calculator_tests
      COMMAND
        ${CMAKE_COMMAND} -E env "PATH=${MINGW_BIN_DIR};$ENV{PATH}"
        $<TARGET_FILE:calculator_tests>
    )
  else()
    add_test(NAME calculator_tests COMMAND calculator_tests)
  endif()
endif()
