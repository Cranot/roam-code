#include <cmath>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>

#include "calculator/engine.h"

TEST_CASE("Arithmetic precedence and associativity") {
    calculator::CalculatorEngine engine;

    CHECK(engine.evaluateExpression("1 + 2 * 3").toString() == "7");
    CHECK(engine.evaluateExpression("(1 + 2) * 3").toString() == "9");
    CHECK(engine.evaluateExpression("2 ^ 3 ^ 2").toString() == "512");
    CHECK(engine.evaluateExpression("7 / 2").asDouble() == Catch::Approx(3.5));
    CHECK(engine.evaluateExpression("7 % 4").toString() == "3");
}

TEST_CASE("Unary minus and nested parentheses") {
    calculator::CalculatorEngine engine;

    CHECK(engine.evaluateExpression("-5").toString() == "-5");
    CHECK(engine.evaluateExpression("-(3 + 2)").toString() == "-5");
    CHECK(engine.evaluateExpression("-(-2)").toString() == "2");
}

TEST_CASE("Built-in functions and constants") {
    calculator::CalculatorEngine engine;

    CHECK(engine.evaluateExpression("sin(pi / 2)").asDouble() == Catch::Approx(1.0).margin(1e-12));
    CHECK(engine.evaluateExpression("sqrt(9)").toString() == "3");
    CHECK(engine.evaluateExpression("log10(1000)").toString() == "3");
    CHECK(engine.evaluateExpression("min(3, 1, 2)").toString() == "1");
    CHECK(engine.evaluateExpression("max(3, 1, 2)").toString() == "3");
}

TEST_CASE("Variable assignment and reuse") {
    calculator::CalculatorEngine engine;

    CHECK(engine.evaluateExpression("x = 3.14").asDouble() == Catch::Approx(3.14));
    CHECK(engine.evaluateExpression("x * 2").asDouble() == Catch::Approx(6.28));

    CHECK_THROWS_WITH(engine.evaluateExpression("pi = 3"), Catch::Matchers::ContainsSubstring("built-in constant"));
}

TEST_CASE("History references") {
    calculator::CalculatorEngine engine;

    CHECK(engine.evaluateExpression("2 + 3").toString() == "5");
    CHECK(engine.evaluateExpression("10").toString() == "10");
    CHECK(engine.evaluateExpression("$1 + $2").toString() == "15");

    CHECK_THROWS_WITH(engine.evaluateExpression("$99"), Catch::Matchers::ContainsSubstring("out of range"));
}

TEST_CASE("Clear parse and evaluation errors") {
    calculator::CalculatorEngine engine;

    CHECK_THROWS_WITH(engine.evaluateExpression("2 + * 3"), Catch::Matchers::ContainsSubstring("Unexpected token '*'"));
    CHECK_THROWS_WITH(engine.evaluateExpression("foo(1)"), Catch::Matchers::ContainsSubstring("Unknown function 'foo'"));
}
