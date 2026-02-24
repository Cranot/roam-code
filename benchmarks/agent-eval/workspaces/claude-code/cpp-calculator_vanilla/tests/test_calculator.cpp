#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include <doctest/doctest.h>
#include "calculator/lexer.h"
#include "calculator/parser.h"
#include "calculator/evaluator.h"
#include "calculator/repl.h"
#include <cmath>
#include <sstream>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#ifndef M_E
#define M_E 2.71828182845904523536
#endif

// ============================================================
// Lexer tests
// ============================================================

TEST_SUITE("Lexer") {
    TEST_CASE("tokenize integer") {
        calculator::Lexer lexer("42");
        auto tokens = lexer.tokenize();
        REQUIRE(tokens.size() == 2);
        CHECK(tokens[0].type == calculator::TokenType::Number);
        CHECK(tokens[0].value == "42");
        CHECK(tokens[1].type == calculator::TokenType::End);
    }

    TEST_CASE("tokenize floating point") {
        calculator::Lexer lexer("3.14");
        auto tokens = lexer.tokenize();
        REQUIRE(tokens.size() == 2);
        CHECK(tokens[0].type == calculator::TokenType::Number);
        CHECK(tokens[0].value == "3.14");
    }

    TEST_CASE("tokenize scientific notation") {
        calculator::Lexer lexer("1.5e10");
        auto tokens = lexer.tokenize();
        REQUIRE(tokens.size() == 2);
        CHECK(tokens[0].type == calculator::TokenType::Number);
        CHECK(tokens[0].value == "1.5e10");
    }

    TEST_CASE("tokenize operators") {
        calculator::Lexer lexer("+ - * / % ^");
        auto tokens = lexer.tokenize();
        REQUIRE(tokens.size() == 7); // 6 operators + End
        CHECK(tokens[0].type == calculator::TokenType::Plus);
        CHECK(tokens[1].type == calculator::TokenType::Minus);
        CHECK(tokens[2].type == calculator::TokenType::Star);
        CHECK(tokens[3].type == calculator::TokenType::Slash);
        CHECK(tokens[4].type == calculator::TokenType::Percent);
        CHECK(tokens[5].type == calculator::TokenType::Caret);
    }

    TEST_CASE("tokenize parentheses and comma") {
        calculator::Lexer lexer("max(1, 2)");
        auto tokens = lexer.tokenize();
        REQUIRE(tokens.size() == 7);
        CHECK(tokens[0].type == calculator::TokenType::Identifier);
        CHECK(tokens[0].value == "max");
        CHECK(tokens[1].type == calculator::TokenType::LParen);
        CHECK(tokens[2].type == calculator::TokenType::Number);
        CHECK(tokens[3].type == calculator::TokenType::Comma);
        CHECK(tokens[4].type == calculator::TokenType::Number);
        CHECK(tokens[5].type == calculator::TokenType::RParen);
    }

    TEST_CASE("tokenize assignment") {
        calculator::Lexer lexer("x = 5");
        auto tokens = lexer.tokenize();
        REQUIRE(tokens.size() == 4);
        CHECK(tokens[0].type == calculator::TokenType::Identifier);
        CHECK(tokens[0].value == "x");
        CHECK(tokens[1].type == calculator::TokenType::Equals);
        CHECK(tokens[2].type == calculator::TokenType::Number);
    }

    TEST_CASE("tokenize history reference") {
        calculator::Lexer lexer("$1 + $23");
        auto tokens = lexer.tokenize();
        REQUIRE(tokens.size() == 4);
        CHECK(tokens[0].type == calculator::TokenType::HistoryRef);
        CHECK(tokens[0].value == "$1");
        CHECK(tokens[2].type == calculator::TokenType::HistoryRef);
        CHECK(tokens[2].value == "$23");
    }

    TEST_CASE("tokenize positions are correct") {
        calculator::Lexer lexer("1 + 2");
        auto tokens = lexer.tokenize();
        CHECK(tokens[0].position == 0);
        CHECK(tokens[1].position == 2);
        CHECK(tokens[2].position == 4);
    }

    TEST_CASE("unexpected character error") {
        calculator::Lexer lexer("2 & 3");
        CHECK_THROWS_AS(lexer.tokenize(), calculator::LexerError);
        try {
            calculator::Lexer l2("2 & 3");
            l2.tokenize();
        } catch (const calculator::LexerError& e) {
            CHECK(e.position == 2);
            std::string msg = e.what();
            CHECK(msg.find("&") != std::string::npos);
            CHECK(msg.find("position 2") != std::string::npos);
        }
    }

    TEST_CASE("invalid history reference") {
        calculator::Lexer lexer("$abc");
        CHECK_THROWS_AS(lexer.tokenize(), calculator::LexerError);
    }
}

// ============================================================
// Parser tests
// ============================================================

TEST_SUITE("Parser") {
    using namespace calculator;

    auto parseExpr = [](const std::string& input) {
        Lexer lexer(input);
        auto tokens = lexer.tokenize();
        Parser parser(tokens);
        return parser.parse();
    };

    TEST_CASE("parse number") {
        auto node = parseExpr("42");
        CHECK(node->type == NodeType::Number);
        CHECK(node->numValue == 42.0);
    }

    TEST_CASE("parse addition") {
        auto node = parseExpr("1 + 2");
        CHECK(node->type == NodeType::BinaryOp);
        CHECK(node->op == "+");
        CHECK(node->children[0]->numValue == 1.0);
        CHECK(node->children[1]->numValue == 2.0);
    }

    TEST_CASE("parse operator precedence: * before +") {
        auto node = parseExpr("1 + 2 * 3");
        CHECK(node->type == NodeType::BinaryOp);
        CHECK(node->op == "+");
        CHECK(node->children[0]->numValue == 1.0);
        CHECK(node->children[1]->type == NodeType::BinaryOp);
        CHECK(node->children[1]->op == "*");
    }

    TEST_CASE("parse parentheses override precedence") {
        auto node = parseExpr("(1 + 2) * 3");
        CHECK(node->type == NodeType::BinaryOp);
        CHECK(node->op == "*");
        CHECK(node->children[0]->type == NodeType::BinaryOp);
        CHECK(node->children[0]->op == "+");
    }

    TEST_CASE("parse power right-associativity") {
        auto node = parseExpr("2 ^ 3 ^ 2");
        CHECK(node->type == NodeType::BinaryOp);
        CHECK(node->op == "^");
        CHECK(node->children[0]->numValue == 2.0);
        // right child should be 3 ^ 2
        CHECK(node->children[1]->type == NodeType::BinaryOp);
        CHECK(node->children[1]->op == "^");
    }

    TEST_CASE("parse unary minus") {
        auto node = parseExpr("-5");
        CHECK(node->type == NodeType::UnaryMinus);
        CHECK(node->children[0]->numValue == 5.0);
    }

    TEST_CASE("parse function call") {
        auto node = parseExpr("sin(3.14)");
        CHECK(node->type == NodeType::FunctionCall);
        CHECK(node->strValue == "sin");
        CHECK(node->children.size() == 1);
    }

    TEST_CASE("parse function call with two arguments") {
        auto node = parseExpr("max(1, 2)");
        CHECK(node->type == NodeType::FunctionCall);
        CHECK(node->strValue == "max");
        CHECK(node->children.size() == 2);
    }

    TEST_CASE("parse variable") {
        auto node = parseExpr("x");
        CHECK(node->type == NodeType::Variable);
        CHECK(node->strValue == "x");
    }

    TEST_CASE("parse assignment") {
        auto node = parseExpr("x = 5");
        CHECK(node->type == NodeType::Assignment);
        CHECK(node->strValue == "x");
        CHECK(node->children[0]->numValue == 5.0);
    }

    TEST_CASE("parse history reference") {
        auto node = parseExpr("$1");
        CHECK(node->type == NodeType::HistoryRef);
        CHECK(node->numValue == 1.0);
    }

    TEST_CASE("parse empty expression throws") {
        CHECK_THROWS_AS(parseExpr(""), ParseError);
    }

    TEST_CASE("parse error on unexpected token") {
        CHECK_THROWS_AS(parseExpr("1 + * 2"), ParseError);
        try {
            parseExpr("1 + * 2");
        } catch (const ParseError& e) {
            std::string msg = e.what();
            CHECK(msg.find("*") != std::string::npos);
            CHECK(msg.find("position") != std::string::npos);
        }
    }

    TEST_CASE("parse error on trailing tokens") {
        CHECK_THROWS_AS(parseExpr("1 2"), ParseError);
    }

    TEST_CASE("parse nested parentheses") {
        auto node = parseExpr("((1 + 2))");
        CHECK(node->type == NodeType::BinaryOp);
        CHECK(node->op == "+");
    }

    TEST_CASE("parse deeply nested parentheses") {
        auto node = parseExpr("(((((1)))))");
        CHECK(node->type == NodeType::Number);
        CHECK(node->numValue == 1.0);
    }
}

// ============================================================
// Evaluator tests
// ============================================================

TEST_SUITE("Evaluator") {
    TEST_CASE("basic arithmetic") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("2 + 3") == doctest::Approx(5.0));
        CHECK(eval.evaluate("10 - 4") == doctest::Approx(6.0));
        CHECK(eval.evaluate("3 * 7") == doctest::Approx(21.0));
        CHECK(eval.evaluate("15 / 4") == doctest::Approx(3.75));
        CHECK(eval.evaluate("17 % 5") == doctest::Approx(2.0));
        CHECK(eval.evaluate("2 ^ 10") == doctest::Approx(1024.0));
    }

    TEST_CASE("operator precedence") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("2 + 3 * 4") == doctest::Approx(14.0));
        CHECK(eval.evaluate("2 * 3 + 4") == doctest::Approx(10.0));
        CHECK(eval.evaluate("2 + 3 * 4 + 5") == doctest::Approx(19.0));
        CHECK(eval.evaluate("10 - 2 * 3") == doctest::Approx(4.0));
    }

    TEST_CASE("parentheses") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("(2 + 3) * 4") == doctest::Approx(20.0));
        CHECK(eval.evaluate("2 * (3 + 4)") == doctest::Approx(14.0));
        CHECK(eval.evaluate("(2 + 3) * (4 + 5)") == doctest::Approx(45.0));
        CHECK(eval.evaluate("((1 + 2) * (3 + 4))") == doctest::Approx(21.0));
    }

    TEST_CASE("unary minus") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("-5") == doctest::Approx(-5.0));
        CHECK(eval.evaluate("-(3 + 2)") == doctest::Approx(-5.0));
        CHECK(eval.evaluate("-(-5)") == doctest::Approx(5.0));
        CHECK(eval.evaluate("2 + -3") == doctest::Approx(-1.0));
        CHECK(eval.evaluate("2 * -3") == doctest::Approx(-6.0));
    }

    TEST_CASE("power right-associativity") {
        calculator::Evaluator eval;
        // 2^3^2 should be 2^(3^2) = 2^9 = 512, not (2^3)^2 = 64
        CHECK(eval.evaluate("2 ^ 3 ^ 2") == doctest::Approx(512.0));
    }

    TEST_CASE("floating point") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("1.5 + 2.5") == doctest::Approx(4.0));
        CHECK(eval.evaluate("0.1 + 0.2") == doctest::Approx(0.3));
        CHECK(eval.evaluate("3.14 * 2") == doctest::Approx(6.28));
    }

    TEST_CASE("scientific notation") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("1e3") == doctest::Approx(1000.0));
        CHECK(eval.evaluate("2.5e2") == doctest::Approx(250.0));
        CHECK(eval.evaluate("1.5e-3") == doctest::Approx(0.0015));
    }

    TEST_CASE("built-in constants") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("pi") == doctest::Approx(M_PI));
        CHECK(eval.evaluate("e") == doctest::Approx(M_E));
    }

    TEST_CASE("functions: trig") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("sin(0)") == doctest::Approx(0.0));
        CHECK(eval.evaluate("cos(0)") == doctest::Approx(1.0));
        CHECK(eval.evaluate("tan(0)") == doctest::Approx(0.0));
        CHECK(eval.evaluate("sin(pi / 2)") == doctest::Approx(1.0));
        CHECK(eval.evaluate("cos(pi)") == doctest::Approx(-1.0));
    }

    TEST_CASE("functions: sqrt, log, log10") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("sqrt(16)") == doctest::Approx(4.0));
        CHECK(eval.evaluate("sqrt(2)") == doctest::Approx(1.41421356));
        CHECK(eval.evaluate("log(e)") == doctest::Approx(1.0));
        CHECK(eval.evaluate("log(1)") == doctest::Approx(0.0));
        CHECK(eval.evaluate("log10(100)") == doctest::Approx(2.0));
        CHECK(eval.evaluate("log10(1000)") == doctest::Approx(3.0));
    }

    TEST_CASE("functions: abs, ceil, floor") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("abs(-5)") == doctest::Approx(5.0));
        CHECK(eval.evaluate("abs(5)") == doctest::Approx(5.0));
        CHECK(eval.evaluate("ceil(2.3)") == doctest::Approx(3.0));
        CHECK(eval.evaluate("ceil(-2.3)") == doctest::Approx(-2.0));
        CHECK(eval.evaluate("floor(2.7)") == doctest::Approx(2.0));
        CHECK(eval.evaluate("floor(-2.7)") == doctest::Approx(-3.0));
    }

    TEST_CASE("functions: min, max") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("min(3, 5)") == doctest::Approx(3.0));
        CHECK(eval.evaluate("max(3, 5)") == doctest::Approx(5.0));
        CHECK(eval.evaluate("min(-1, 1)") == doctest::Approx(-1.0));
        CHECK(eval.evaluate("max(-1, -5)") == doctest::Approx(-1.0));
    }

    TEST_CASE("nested function calls") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("sqrt(abs(-16))") == doctest::Approx(4.0));
        CHECK(eval.evaluate("max(sin(0), cos(0))") == doctest::Approx(1.0));
        CHECK(eval.evaluate("abs(min(-3, -5))") == doctest::Approx(5.0));
    }

    TEST_CASE("variable assignment and use") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("x = 42") == doctest::Approx(42.0));
        CHECK(eval.evaluate("x") == doctest::Approx(42.0));
        CHECK(eval.evaluate("x + 8") == doctest::Approx(50.0));
        CHECK(eval.evaluate("y = x * 2") == doctest::Approx(84.0));
        CHECK(eval.evaluate("y") == doctest::Approx(84.0));
    }

    TEST_CASE("variable reassignment") {
        calculator::Evaluator eval;
        eval.evaluate("x = 1");
        eval.evaluate("x = 2");
        CHECK(eval.evaluate("x") == doctest::Approx(2.0));
    }

    TEST_CASE("cannot reassign constants") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("pi = 3"), calculator::EvalError);
        CHECK_THROWS_AS(eval.evaluate("e = 3"), calculator::EvalError);
    }

    TEST_CASE("history references") {
        calculator::Evaluator eval;
        eval.evaluate("10");    // $1 = 10
        eval.evaluate("20");    // $2 = 20
        CHECK(eval.evaluate("$1 + $2") == doctest::Approx(30.0));
    }

    TEST_CASE("history out of range") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("$1"), calculator::EvalError);
        eval.evaluate("42");
        CHECK_THROWS_AS(eval.evaluate("$2"), calculator::EvalError);
        CHECK_THROWS_AS(eval.evaluate("$0"), calculator::EvalError);
    }

    TEST_CASE("division by zero") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("1 / 0"), calculator::EvalError);
    }

    TEST_CASE("modulo by zero") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("5 % 0"), calculator::EvalError);
    }

    TEST_CASE("sqrt of negative") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("sqrt(-1)"), calculator::EvalError);
    }

    TEST_CASE("log of non-positive") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("log(0)"), calculator::EvalError);
        CHECK_THROWS_AS(eval.evaluate("log(-1)"), calculator::EvalError);
        CHECK_THROWS_AS(eval.evaluate("log10(0)"), calculator::EvalError);
    }

    TEST_CASE("unknown variable") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("unknown_var"), calculator::EvalError);
        try {
            eval.evaluate("foo");
        } catch (const calculator::EvalError& e) {
            std::string msg = e.what();
            CHECK(msg.find("Unknown variable") != std::string::npos);
            CHECK(msg.find("foo") != std::string::npos);
        }
    }

    TEST_CASE("unknown function") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("foo(1)"), calculator::EvalError);
        try {
            eval.evaluate("bar(1)");
        } catch (const calculator::EvalError& e) {
            std::string msg = e.what();
            CHECK(msg.find("Unknown function") != std::string::npos);
            CHECK(msg.find("bar") != std::string::npos);
        }
    }

    TEST_CASE("wrong number of arguments") {
        calculator::Evaluator eval;
        CHECK_THROWS_AS(eval.evaluate("sin(1, 2)"), calculator::EvalError);
        CHECK_THROWS_AS(eval.evaluate("max(1)"), calculator::EvalError);
    }

    TEST_CASE("complex expressions") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("2 + 3 * 4 - 1") == doctest::Approx(13.0));
        CHECK(eval.evaluate("(2 + 3) * (4 - 1)") == doctest::Approx(15.0));
        CHECK(eval.evaluate("2 ^ 3 + 1") == doctest::Approx(9.0));
        CHECK(eval.evaluate("sqrt(3^2 + 4^2)") == doctest::Approx(5.0));
        CHECK(eval.evaluate("-2 ^ 2") == doctest::Approx(-4.0)); // -(2^2), not (-2)^2
    }

    TEST_CASE("unary minus precedence vs power") {
        calculator::Evaluator eval;
        // -2^2 should be -(2^2) = -4, since power binds tighter than unary minus
        CHECK(eval.evaluate("-2 ^ 2") == doctest::Approx(-4.0));
        CHECK(eval.evaluate("(-2) ^ 2") == doctest::Approx(4.0));
    }
}

// ============================================================
// REPL integration tests
// ============================================================

TEST_SUITE("REPL") {
    TEST_CASE("REPL processes expressions") {
        calculator::REPL repl;
        std::istringstream input("2 + 3\nquit\n");
        std::ostringstream output;
        repl.run(input, output);
        std::string result = output.str();
        CHECK(result.find("5") != std::string::npos);
    }

    TEST_CASE("REPL help command") {
        calculator::REPL repl;
        std::istringstream input("help\nquit\n");
        std::ostringstream output;
        repl.run(input, output);
        std::string result = output.str();
        CHECK(result.find("Functions:") != std::string::npos);
        CHECK(result.find("sin") != std::string::npos);
    }

    TEST_CASE("REPL variable persistence") {
        calculator::REPL repl;
        std::istringstream input("x = 10\nx * 2\nquit\n");
        std::ostringstream output;
        repl.run(input, output);
        std::string result = output.str();
        CHECK(result.find("10") != std::string::npos);
        CHECK(result.find("20") != std::string::npos);
    }

    TEST_CASE("REPL error messages") {
        calculator::REPL repl;
        std::istringstream input("1 / 0\nfoo(1)\nquit\n");
        std::ostringstream output;
        repl.run(input, output);
        std::string result = output.str();
        CHECK(result.find("Error:") != std::string::npos);
    }

    TEST_CASE("REPL history command") {
        calculator::REPL repl;
        std::istringstream input("42\nhistory\nquit\n");
        std::ostringstream output;
        repl.run(input, output);
        std::string result = output.str();
        CHECK(result.find("$1") != std::string::npos);
    }

    TEST_CASE("REPL file evaluation") {
        // Create a temp file
        std::string tmpFile = "test_expressions.txt";
        {
            std::ofstream f(tmpFile);
            f << "2 + 3\n"
              << "# this is a comment\n"
              << "10 * 4\n"
              << "sqrt(16)\n";
        }

        calculator::REPL repl;
        std::ostringstream output;
        repl.evaluateFile(tmpFile, output);
        std::string result = output.str();
        CHECK(result.find("5") != std::string::npos);
        CHECK(result.find("40") != std::string::npos);
        CHECK(result.find("4") != std::string::npos);

        // Clean up
        std::remove(tmpFile.c_str());
    }

    TEST_CASE("REPL exit command works") {
        calculator::REPL repl;
        std::istringstream input("exit\n");
        std::ostringstream output;
        repl.run(input, output);
        // Should exit cleanly
        CHECK(true);
    }

    TEST_CASE("REPL handles empty lines") {
        calculator::REPL repl;
        std::istringstream input("\n\n  \n2 + 2\nquit\n");
        std::ostringstream output;
        repl.run(input, output);
        std::string result = output.str();
        CHECK(result.find("4") != std::string::npos);
    }

    TEST_CASE("REPL vars command") {
        calculator::REPL repl;
        std::istringstream input("myvar = 99\nvars\nquit\n");
        std::ostringstream output;
        repl.run(input, output);
        std::string result = output.str();
        CHECK(result.find("myvar") != std::string::npos);
        CHECK(result.find("99") != std::string::npos);
    }

    TEST_CASE("REPL clear command") {
        calculator::REPL repl;
        std::istringstream input("42\nclear\nhistory\nquit\n");
        std::ostringstream output;
        repl.run(input, output);
        std::string result = output.str();
        CHECK(result.find("History cleared") != std::string::npos);
    }
}

// ============================================================
// Edge case tests
// ============================================================

TEST_SUITE("Edge Cases") {
    TEST_CASE("whitespace handling") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("  2  +  3  ") == doctest::Approx(5.0));
        CHECK(eval.evaluate("2+3") == doctest::Approx(5.0));
    }

    TEST_CASE("leading decimal point") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate(".5 + .5") == doctest::Approx(1.0));
    }

    TEST_CASE("multiple operations chained") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("1 + 2 + 3 + 4 + 5") == doctest::Approx(15.0));
        CHECK(eval.evaluate("2 * 3 * 4") == doctest::Approx(24.0));
    }

    TEST_CASE("mixed operations") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("2 + 3 * 4 / 2 - 1") == doctest::Approx(7.0));
    }

    TEST_CASE("functions in expressions") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("1 + sin(0)") == doctest::Approx(1.0));
        CHECK(eval.evaluate("2 * sqrt(4)") == doctest::Approx(4.0));
        CHECK(eval.evaluate("sqrt(4) + sqrt(9)") == doctest::Approx(5.0));
    }

    TEST_CASE("variable in function argument") {
        calculator::Evaluator eval;
        eval.evaluate("x = 16");
        CHECK(eval.evaluate("sqrt(x)") == doctest::Approx(4.0));
    }

    TEST_CASE("assignment expression value") {
        calculator::Evaluator eval;
        // Assignment should return the assigned value
        CHECK(eval.evaluate("x = 5 + 3") == doctest::Approx(8.0));
    }

    TEST_CASE("history in expressions") {
        calculator::Evaluator eval;
        eval.evaluate("100"); // $1
        eval.evaluate("200"); // $2
        CHECK(eval.evaluate("$1 + $2") == doctest::Approx(300.0));
        CHECK(eval.evaluate("$3") == doctest::Approx(300.0)); // result of the above
    }

    TEST_CASE("modulo with floating point") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("5.5 % 2") == doctest::Approx(1.5));
    }

    TEST_CASE("unary plus") {
        calculator::Evaluator eval;
        CHECK(eval.evaluate("+5") == doctest::Approx(5.0));
        CHECK(eval.evaluate("+(3 + 2)") == doctest::Approx(5.0));
    }
}
