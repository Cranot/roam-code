include(FetchContent)

FetchContent_Declare(
    doctest
    GIT_REPOSITORY https://github.com/doctest/doctest.git
    GIT_TAG v2.4.11
)
FetchContent_MakeAvailable(doctest)

add_executable(test_calculator test_calculator.cpp)
target_link_libraries(test_calculator PRIVATE calculator_lib doctest::doctest)

include(${doctest_SOURCE_DIR}/scripts/cmake/doctest.cmake)
doctest_discover_tests(test_calculator)
