#pragma once

#include <cstddef>
#include <string>

namespace calculator {

enum class TokenType {
    EndOfInput,
    Number,
    Identifier,
    HistoryRef,
    Plus,
    Minus,
    Star,
    Slash,
    Percent,
    Caret,
    LParen,
    RParen,
    Comma,
    Assign,
};

struct Token {
    TokenType type;
    std::string lexeme;
    std::size_t position;
};

std::string tokenTypeToString(TokenType type);

}  // namespace calculator