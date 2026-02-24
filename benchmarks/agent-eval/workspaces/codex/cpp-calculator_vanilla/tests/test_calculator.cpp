#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include <doctest/doctest.h>

#include <string>

#include "calculator/calculator.h"
#include "calculator/errors.h"

namespace {

bool contains(const std::string& text, const std::string& pattern) {
  return text.find(pattern) != std::string::npos;
}

}  // namespace

TEST_CASE("operator precedence and arithmetic") {
  calc::Calculator calculator;

  CHECK(calculator.evaluate("2 + 3 * 4") == doctest::Approx(14.0));
  CHECK(calculator.evaluate("(2 + 3) * 4") == doctest::Approx(20.0));
  CHECK(calculator.evaluate("20 / 5 + 1") == doctest::Approx(5.0));
  CHECK(calculator.evaluate("20 % 6") == doctest::Approx(2.0));
}

TEST_CASE("power is right-associative") {
  calc::Calculator calculator;
  CHECK(calculator.evaluate("2 ^ 3 ^ 2") == doctest::Approx(512.0));
}

TEST_CASE("unary minus") {
  calc::Calculator calculator;

  CHECK(calculator.evaluate("-5") == doctest::Approx(-5.0));
  CHECK(calculator.evaluate("-(3 + 2)") == doctest::Approx(-5.0));
}

TEST_CASE("built-in functions and constants") {
  calc::Calculator calculator;

  CHECK(calculator.evaluate("sin(pi / 2)") == doctest::Approx(1.0));
  CHECK(calculator.evaluate("cos(0)") == doctest::Approx(1.0));
  CHECK(calculator.evaluate("sqrt(9)") == doctest::Approx(3.0));
  CHECK(calculator.evaluate("log(e)") == doctest::Approx(1.0));
  CHECK(calculator.evaluate("log10(1000)") == doctest::Approx(3.0));
  CHECK(calculator.evaluate("abs(-8)") == doctest::Approx(8.0));
  CHECK(calculator.evaluate("ceil(3.1)") == doctest::Approx(4.0));
  CHECK(calculator.evaluate("floor(3.9)") == doctest::Approx(3.0));
  CHECK(calculator.evaluate("min(4, -2, 7)") == doctest::Approx(-2.0));
  CHECK(calculator.evaluate("max(4, -2, 7)") == doctest::Approx(7.0));
}

TEST_CASE("variable assignment and reuse") {
  calc::Calculator calculator;

  CHECK(calculator.evaluate("x = 3.14") == doctest::Approx(3.14));
  CHECK(calculator.evaluate("x * 2") == doctest::Approx(6.28));
}

TEST_CASE("history recall") {
  calc::Calculator calculator;

  CHECK(calculator.evaluate("2 + 3") == doctest::Approx(5.0));
  CHECK(calculator.evaluate("7") == doctest::Approx(7.0));
  CHECK(calculator.evaluate("$1 + $2") == doctest::Approx(12.0));
}

TEST_CASE("unknown function error") {
  calc::Calculator calculator;

  try {
    calculator.evaluate("foo(1)");
    FAIL("Expected EvalError");
  } catch (const calc::EvalError& ex) {
    CHECK(contains(ex.what(), "Unknown function 'foo'"));
  }
}

TEST_CASE("parse error includes token and position") {
  calc::Calculator calculator;

  try {
    calculator.evaluate("2 + * 3");
    FAIL("Expected ParseError");
  } catch (const calc::ParseError& ex) {
    CHECK(contains(ex.what(), "Unexpected token '*'"));
    CHECK(contains(ex.what(), "position 5"));
  }
}

TEST_CASE("unknown variable error") {
  calc::Calculator calculator;

  try {
    calculator.evaluate("missing_var + 1");
    FAIL("Expected EvalError");
  } catch (const calc::EvalError& ex) {
    CHECK(contains(ex.what(), "Unknown variable 'missing_var'"));
  }
}