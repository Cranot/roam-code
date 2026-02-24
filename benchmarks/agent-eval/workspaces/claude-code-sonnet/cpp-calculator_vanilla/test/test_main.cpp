#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>
#include "Lexer.h"
#include "Parser.h"
#include "Evaluator.h"
#include "AST.h"
#include <cmath>

using Catch::Matchers::WithinAbs;

// Helper function to evaluate an expression
double eval(const std::string& input) {
    Lexer lexer(input);
    std::vector<Token> tokens = lexer.tokenize();
    Parser parser(tokens);
    auto ast = parser.parse();
    Evaluator evaluator;
    return ast->evaluate(evaluator);
}

// Helper function with existing evaluator
double eval_with(const std::string& input, Evaluator& evaluator) {
    Lexer lexer(input);
    std::vector<Token> tokens = lexer.tokenize();
    Parser parser(tokens);
    auto ast = parser.parse();
    return ast->evaluate(evaluator);
}

TEST_CASE("Lexer tokenizes numbers correctly", "[lexer]") {
    Lexer lexer("123 45.67 .5");
    auto tokens = lexer.tokenize();

    REQUIRE(tokens.size() == 4); // 3 numbers + END
    REQUIRE(tokens[0].type == TokenType::NUMBER);
    REQUIRE(tokens[0].value == "123");
    REQUIRE(tokens[1].type == TokenType::NUMBER);
    REQUIRE(tokens[1].value == "45.67");
    REQUIRE(tokens[2].type == TokenType::NUMBER);
    REQUIRE(tokens[2].value == ".5");
}

TEST_CASE("Lexer tokenizes operators correctly", "[lexer]") {
    Lexer lexer("+ - * / % ^");
    auto tokens = lexer.tokenize();

    REQUIRE(tokens.size() == 7); // 6 operators + END
    REQUIRE(tokens[0].type == TokenType::PLUS);
    REQUIRE(tokens[1].type == TokenType::MINUS);
    REQUIRE(tokens[2].type == TokenType::MULTIPLY);
    REQUIRE(tokens[3].type == TokenType::DIVIDE);
    REQUIRE(tokens[4].type == TokenType::MODULO);
    REQUIRE(tokens[5].type == TokenType::POWER);
}

TEST_CASE("Lexer tokenizes identifiers and symbols", "[lexer]") {
    Lexer lexer("x = sin(y)");
    auto tokens = lexer.tokenize();

    REQUIRE(tokens[0].type == TokenType::IDENTIFIER);
    REQUIRE(tokens[0].value == "x");
    REQUIRE(tokens[1].type == TokenType::ASSIGN);
    REQUIRE(tokens[2].type == TokenType::IDENTIFIER);
    REQUIRE(tokens[2].value == "sin");
    REQUIRE(tokens[3].type == TokenType::LPAREN);
    REQUIRE(tokens[4].type == TokenType::IDENTIFIER);
    REQUIRE(tokens[4].value == "y");
    REQUIRE(tokens[5].type == TokenType::RPAREN);
}

TEST_CASE("Basic arithmetic operations", "[evaluator]") {
    REQUIRE_THAT(eval("2 + 3"), WithinAbs(5.0, 0.0001));
    REQUIRE_THAT(eval("10 - 4"), WithinAbs(6.0, 0.0001));
    REQUIRE_THAT(eval("3 * 7"), WithinAbs(21.0, 0.0001));
    REQUIRE_THAT(eval("15 / 3"), WithinAbs(5.0, 0.0001));
    REQUIRE_THAT(eval("17 % 5"), WithinAbs(2.0, 0.0001));
}

TEST_CASE("Operator precedence", "[evaluator]") {
    REQUIRE_THAT(eval("2 + 3 * 4"), WithinAbs(14.0, 0.0001)); // not 20
    REQUIRE_THAT(eval("10 - 2 * 3"), WithinAbs(4.0, 0.0001)); // not 24
    REQUIRE_THAT(eval("20 / 4 + 1"), WithinAbs(6.0, 0.0001));
}

TEST_CASE("Parentheses grouping", "[evaluator]") {
    REQUIRE_THAT(eval("(2 + 3) * 4"), WithinAbs(20.0, 0.0001));
    REQUIRE_THAT(eval("2 * (3 + 4)"), WithinAbs(14.0, 0.0001));
    REQUIRE_THAT(eval("((2 + 3) * (4 + 5))"), WithinAbs(45.0, 0.0001));
}

TEST_CASE("Nested parentheses", "[evaluator]") {
    REQUIRE_THAT(eval("((2 + 3))"), WithinAbs(5.0, 0.0001));
    REQUIRE_THAT(eval("(((1 + 2) * 3) + 4)"), WithinAbs(13.0, 0.0001));
}

TEST_CASE("Unary minus", "[evaluator]") {
    REQUIRE_THAT(eval("-5"), WithinAbs(-5.0, 0.0001));
    REQUIRE_THAT(eval("-(3 + 2)"), WithinAbs(-5.0, 0.0001));
    REQUIRE_THAT(eval("-3 * 4"), WithinAbs(-12.0, 0.0001));
    REQUIRE_THAT(eval("5 + -3"), WithinAbs(2.0, 0.0001));
}

TEST_CASE("Unary plus", "[evaluator]") {
    REQUIRE_THAT(eval("+5"), WithinAbs(5.0, 0.0001));
    REQUIRE_THAT(eval("+(3 + 2)"), WithinAbs(5.0, 0.0001));
}

TEST_CASE("Power operator", "[evaluator]") {
    REQUIRE_THAT(eval("2 ^ 3"), WithinAbs(8.0, 0.0001));
    REQUIRE_THAT(eval("4 ^ 0.5"), WithinAbs(2.0, 0.0001));
    REQUIRE_THAT(eval("2 ^ 3 ^ 2"), WithinAbs(512.0, 0.0001)); // Right-associative: 2^(3^2) = 2^9
}

TEST_CASE("Trigonometric functions", "[evaluator]") {
    REQUIRE_THAT(eval("sin(0)"), WithinAbs(0.0, 0.0001));
    REQUIRE_THAT(eval("cos(0)"), WithinAbs(1.0, 0.0001));
    REQUIRE_THAT(eval("tan(0)"), WithinAbs(0.0, 0.0001));

    Evaluator evaluator;
    double pi = M_PI;
    REQUIRE_THAT(eval_with("sin(pi/2)", evaluator), WithinAbs(1.0, 0.0001));
    REQUIRE_THAT(eval_with("cos(pi)", evaluator), WithinAbs(-1.0, 0.0001));
}

TEST_CASE("Mathematical functions", "[evaluator]") {
    REQUIRE_THAT(eval("sqrt(16)"), WithinAbs(4.0, 0.0001));
    REQUIRE_THAT(eval("sqrt(2)"), WithinAbs(1.41421356, 0.0001));
    REQUIRE_THAT(eval("abs(-5)"), WithinAbs(5.0, 0.0001));
    REQUIRE_THAT(eval("abs(5)"), WithinAbs(5.0, 0.0001));
    REQUIRE_THAT(eval("ceil(3.2)"), WithinAbs(4.0, 0.0001));
    REQUIRE_THAT(eval("floor(3.8)"), WithinAbs(3.0, 0.0001));
}

TEST_CASE("Logarithmic functions", "[evaluator]") {
    REQUIRE_THAT(eval("log(2.71828)"), WithinAbs(1.0, 0.001));
    REQUIRE_THAT(eval("log10(100)"), WithinAbs(2.0, 0.0001));
    REQUIRE_THAT(eval("log10(1000)"), WithinAbs(3.0, 0.0001));
}

TEST_CASE("Min and max functions", "[evaluator]") {
    REQUIRE_THAT(eval("min(1, 2, 3)"), WithinAbs(1.0, 0.0001));
    REQUIRE_THAT(eval("min(5, 2)"), WithinAbs(2.0, 0.0001));
    REQUIRE_THAT(eval("max(1, 2, 3)"), WithinAbs(3.0, 0.0001));
    REQUIRE_THAT(eval("max(5, 10, 3)"), WithinAbs(10.0, 0.0001));
}

TEST_CASE("Constants pi and e", "[evaluator]") {
    REQUIRE_THAT(eval("pi"), WithinAbs(M_PI, 0.0001));
    REQUIRE_THAT(eval("e"), WithinAbs(M_E, 0.0001));
    REQUIRE_THAT(eval("2 * pi"), WithinAbs(2 * M_PI, 0.0001));
}

TEST_CASE("Variable assignment", "[evaluator]") {
    Evaluator evaluator;

    double result1 = eval_with("x = 5", evaluator);
    REQUIRE_THAT(result1, WithinAbs(5.0, 0.0001));

    double result2 = eval_with("x + 3", evaluator);
    REQUIRE_THAT(result2, WithinAbs(8.0, 0.0001));

    double result3 = eval_with("y = x * 2", evaluator);
    REQUIRE_THAT(result3, WithinAbs(10.0, 0.0001));

    double result4 = eval_with("x + y", evaluator);
    REQUIRE_THAT(result4, WithinAbs(15.0, 0.0001));
}

TEST_CASE("Expression history", "[evaluator]") {
    Evaluator evaluator;

    double result1 = eval_with("2 + 3", evaluator);
    evaluator.add_to_history(result1);

    double result2 = eval_with("4 * 5", evaluator);
    evaluator.add_to_history(result2);

    double result3 = eval_with("$1 + $2", evaluator);
    REQUIRE_THAT(result3, WithinAbs(25.0, 0.0001)); // 5 + 20

    evaluator.add_to_history(result3);

    double result4 = eval_with("$3 * 2", evaluator);
    REQUIRE_THAT(result4, WithinAbs(50.0, 0.0001)); // 25 * 2
}

TEST_CASE("Complex expressions", "[evaluator]") {
    Evaluator evaluator;

    // Quadratic formula example
    eval_with("a = 1", evaluator);
    eval_with("b = -5", evaluator);
    eval_with("c = 6", evaluator);

    double discriminant = eval_with("b^2 - 4*a*c", evaluator);
    REQUIRE_THAT(discriminant, WithinAbs(1.0, 0.0001)); // 25 - 24

    double solution = eval_with("(-b + sqrt(b^2 - 4*a*c)) / (2*a)", evaluator);
    REQUIRE_THAT(solution, WithinAbs(3.0, 0.0001));
}

TEST_CASE("Error handling - division by zero", "[evaluator]") {
    REQUIRE_THROWS_AS(eval("1 / 0"), EvaluationError);
    REQUIRE_THROWS_AS(eval("10 % 0"), EvaluationError);
}

TEST_CASE("Error handling - unknown variable", "[evaluator]") {
    REQUIRE_THROWS_AS(eval("unknown_var + 5"), EvaluationError);
}

TEST_CASE("Error handling - unknown function", "[evaluator]") {
    REQUIRE_THROWS_AS(eval("foo(5)"), EvaluationError);
}

TEST_CASE("Error handling - invalid history reference", "[evaluator]") {
    Evaluator evaluator;
    REQUIRE_THROWS_AS(eval_with("$1", evaluator), EvaluationError); // No history yet

    eval_with("5 + 5", evaluator);
    evaluator.add_to_history(10.0);

    REQUIRE_THROWS_AS(eval_with("$2", evaluator), EvaluationError); // Only 1 item in history
}

TEST_CASE("Error handling - wrong number of arguments", "[evaluator]") {
    REQUIRE_THROWS_AS(eval("sin(1, 2)"), EvaluationError);
    REQUIRE_THROWS_AS(eval("sqrt()"), EvaluationError);
    REQUIRE_THROWS_AS(eval("min()"), EvaluationError);
}

TEST_CASE("Error handling - parse errors", "[parser]") {
    REQUIRE_THROWS_AS(eval("2 +"), ParseError);
    REQUIRE_THROWS_AS(eval("* 3"), ParseError);
    REQUIRE_THROWS_AS(eval("(2 + 3"), ParseError);
    REQUIRE_THROWS_AS(eval("2 + 3)"), ParseError);
}

TEST_CASE("Floating point operations", "[evaluator]") {
    REQUIRE_THAT(eval("3.14 * 2"), WithinAbs(6.28, 0.0001));
    REQUIRE_THAT(eval("0.1 + 0.2"), WithinAbs(0.3, 0.0001));
    REQUIRE_THAT(eval("5.5 / 2.2"), WithinAbs(2.5, 0.0001));
}

TEST_CASE("Integer and float mixed operations", "[evaluator]") {
    REQUIRE_THAT(eval("5 + 2.5"), WithinAbs(7.5, 0.0001));
    REQUIRE_THAT(eval("10 / 4.0"), WithinAbs(2.5, 0.0001));
    REQUIRE_THAT(eval("3 * 1.5"), WithinAbs(4.5, 0.0001));
}
