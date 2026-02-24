#ifndef TOKEN_H
#define TOKEN_H

#include <string>
#include <ostream>

enum class TokenType {
    NUMBER,
    IDENTIFIER,
    PLUS,
    MINUS,
    MULTIPLY,
    DIVIDE,
    MODULO,
    POWER,
    LPAREN,
    RPAREN,
    COMMA,
    ASSIGN,
    END,
    INVALID
};

class Token {
public:
    TokenType type;
    std::string value;
    size_t position;

    Token(TokenType type = TokenType::INVALID, const std::string& value = "", size_t position = 0);

    std::string typeToString() const;
    friend std::ostream& operator<<(std::ostream& os, const Token& token);
};

#endif // TOKEN_H
