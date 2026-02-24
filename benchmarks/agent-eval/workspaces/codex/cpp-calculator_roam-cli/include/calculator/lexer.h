#pragma once

#include <string>
#include <vector>

#include "calculator/token.h"

namespace calculator {

class Lexer {
public:
    explicit Lexer(std::string input);

    std::vector<Token> tokenize();

private:
    bool isAtEnd() const;
    char peek() const;
    char peekNext() const;
    char advance();

    void skipWhitespace();
    Token lexNumber();
    Token lexIdentifier();
    Token lexHistoryRef();

    std::string input_;
    std::size_t current_;
};

}  // namespace calculator