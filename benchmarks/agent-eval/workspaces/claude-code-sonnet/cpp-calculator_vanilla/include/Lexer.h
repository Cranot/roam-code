#ifndef LEXER_H
#define LEXER_H

#include "Token.h"
#include <string>
#include <vector>

class Lexer {
private:
    std::string input;
    size_t position;
    char current_char;

    void advance();
    void skip_whitespace();
    Token number();
    Token identifier();
    char peek() const;

public:
    explicit Lexer(const std::string& input);

    std::vector<Token> tokenize();
    Token next_token();
};

#endif // LEXER_H
