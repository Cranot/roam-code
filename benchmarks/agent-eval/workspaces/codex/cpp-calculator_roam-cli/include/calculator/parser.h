#pragma once

#include <vector>

#include "calculator/ast.h"
#include "calculator/token.h"

namespace calculator {

class Parser {
public:
    explicit Parser(std::vector<Token> tokens);

    ExprPtr parse();

private:
    ExprPtr parseAssignment();
    ExprPtr parseAdditive();
    ExprPtr parseMultiplicative();
    ExprPtr parsePower();
    ExprPtr parseUnary();
    ExprPtr parsePrimary();

    bool match(TokenType type);
    bool check(TokenType type) const;
    const Token& advance();
    const Token& peek() const;
    const Token& previous() const;
    bool isAtEnd() const;
    const Token& expect(TokenType type, const std::string& message);

    std::vector<Token> tokens_;
    std::size_t current_;
};

}  // namespace calculator