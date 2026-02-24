#ifndef CALCULATOR_LEXER_H
#define CALCULATOR_LEXER_H

#include <string>
#include <vector>
#include <stdexcept>

namespace calculator {

enum class TokenType {
    Number,
    Identifier,
    Plus,
    Minus,
    Star,
    Slash,
    Percent,
    Caret,
    LParen,
    RParen,
    Comma,
    Equals,
    HistoryRef,  // $1, $2, etc.
    End
};

struct Token {
    TokenType type;
    std::string value;
    int position;

    Token(TokenType type, std::string value, int position)
        : type(type), value(std::move(value)), position(position) {}
};

class LexerError : public std::runtime_error {
public:
    int position;
    LexerError(const std::string& msg, int pos)
        : std::runtime_error(msg), position(pos) {}
};

class Lexer {
public:
    explicit Lexer(const std::string& input);
    std::vector<Token> tokenize();

private:
    std::string input_;
    int pos_;

    char peek() const;
    char advance();
    void skipWhitespace();
    Token readNumber();
    Token readIdentifier();
    Token readHistoryRef();
};

std::string tokenTypeName(TokenType type);

} // namespace calculator

#endif // CALCULATOR_LEXER_H
