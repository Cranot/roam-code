#include "calculator/lexer.h"

#include <cctype>
#include <string>

#include "calculator/errors.h"

namespace calculator {

namespace {

bool isIdentifierStart(char value) {
    return std::isalpha(static_cast<unsigned char>(value)) || value == '_';
}

bool isIdentifierPart(char value) {
    return std::isalnum(static_cast<unsigned char>(value)) || value == '_';
}

std::string quotedLexeme(char value) {
    std::string result;
    result.push_back('\'');
    result.push_back(value);
    result.push_back('\'');
    return result;
}

}  // namespace

Lexer::Lexer(std::string input) : input_(std::move(input)), current_(0) {}

std::vector<Token> Lexer::tokenize() {
    std::vector<Token> tokens;

    while (!isAtEnd()) {
        skipWhitespace();
        if (isAtEnd()) {
            break;
        }

        const char ch = peek();
        if (std::isdigit(static_cast<unsigned char>(ch)) || (ch == '.' && std::isdigit(static_cast<unsigned char>(peekNext())))) {
            tokens.push_back(lexNumber());
            continue;
        }
        if (isIdentifierStart(ch)) {
            tokens.push_back(lexIdentifier());
            continue;
        }
        if (ch == '$') {
            tokens.push_back(lexHistoryRef());
            continue;
        }

        const std::size_t position = current_;
        switch (advance()) {
            case '+':
                tokens.push_back({TokenType::Plus, "+", position});
                break;
            case '-':
                tokens.push_back({TokenType::Minus, "-", position});
                break;
            case '*':
                tokens.push_back({TokenType::Star, "*", position});
                break;
            case '/':
                tokens.push_back({TokenType::Slash, "/", position});
                break;
            case '%':
                tokens.push_back({TokenType::Percent, "%", position});
                break;
            case '^':
                tokens.push_back({TokenType::Caret, "^", position});
                break;
            case '(':
                tokens.push_back({TokenType::LParen, "(", position});
                break;
            case ')':
                tokens.push_back({TokenType::RParen, ")", position});
                break;
            case ',':
                tokens.push_back({TokenType::Comma, ",", position});
                break;
            case '=':
                tokens.push_back({TokenType::Assign, "=", position});
                break;
            default:
                throw LexerError("Unexpected character " + quotedLexeme(input_[position]) + " at position " +
                                 std::to_string(position));
        }
    }

    tokens.push_back({TokenType::EndOfInput, "", input_.size()});
    return tokens;
}

bool Lexer::isAtEnd() const {
    return current_ >= input_.size();
}

char Lexer::peek() const {
    if (isAtEnd()) {
        return '\0';
    }
    return input_[current_];
}

char Lexer::peekNext() const {
    if (current_ + 1 >= input_.size()) {
        return '\0';
    }
    return input_[current_ + 1];
}

char Lexer::advance() {
    return input_[current_++];
}

void Lexer::skipWhitespace() {
    while (!isAtEnd() && std::isspace(static_cast<unsigned char>(peek()))) {
        advance();
    }
}

Token Lexer::lexNumber() {
    const std::size_t start = current_;
    bool saw_dot = false;

    if (peek() == '.') {
        saw_dot = true;
        advance();
    }

    while (std::isdigit(static_cast<unsigned char>(peek()))) {
        advance();
    }

    if (peek() == '.' && !saw_dot) {
        saw_dot = true;
        advance();
        while (std::isdigit(static_cast<unsigned char>(peek()))) {
            advance();
        }
    }

    if (peek() == 'e' || peek() == 'E') {
        advance();
        if (peek() == '+' || peek() == '-') {
            advance();
        }
        if (!std::isdigit(static_cast<unsigned char>(peek()))) {
            throw LexerError("Malformed exponent at position " + std::to_string(current_));
        }
        while (std::isdigit(static_cast<unsigned char>(peek()))) {
            advance();
        }
    }

    return {TokenType::Number, input_.substr(start, current_ - start), start};
}

Token Lexer::lexIdentifier() {
    const std::size_t start = current_;
    while (isIdentifierPart(peek())) {
        advance();
    }
    return {TokenType::Identifier, input_.substr(start, current_ - start), start};
}

Token Lexer::lexHistoryRef() {
    const std::size_t start = current_;
    advance();  // '$'
    if (!std::isdigit(static_cast<unsigned char>(peek()))) {
        throw LexerError("Expected history index after '$' at position " + std::to_string(start));
    }
    while (std::isdigit(static_cast<unsigned char>(peek()))) {
        advance();
    }
    return {TokenType::HistoryRef, input_.substr(start, current_ - start), start};
}

}  // namespace calculator