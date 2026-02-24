#include "calculator/lexer.h"
#include <cctype>
#include <sstream>

namespace calculator {

std::string tokenTypeName(TokenType type) {
    switch (type) {
        case TokenType::Number:     return "number";
        case TokenType::Identifier: return "identifier";
        case TokenType::Plus:       return "'+'";
        case TokenType::Minus:      return "'-'";
        case TokenType::Star:       return "'*'";
        case TokenType::Slash:      return "'/'";
        case TokenType::Percent:    return "'%'";
        case TokenType::Caret:      return "'^'";
        case TokenType::LParen:     return "'('";
        case TokenType::RParen:     return "')'";
        case TokenType::Comma:      return "','";
        case TokenType::Equals:     return "'='";
        case TokenType::HistoryRef: return "history reference";
        case TokenType::End:        return "end of input";
    }
    return "unknown";
}

Lexer::Lexer(const std::string& input) : input_(input), pos_(0) {}

char Lexer::peek() const {
    if (pos_ >= static_cast<int>(input_.size())) return '\0';
    return input_[pos_];
}

char Lexer::advance() {
    return input_[pos_++];
}

void Lexer::skipWhitespace() {
    while (pos_ < static_cast<int>(input_.size()) && std::isspace(input_[pos_])) {
        pos_++;
    }
}

Token Lexer::readNumber() {
    int start = pos_;
    bool hasDot = false;

    while (pos_ < static_cast<int>(input_.size()) &&
           (std::isdigit(input_[pos_]) || input_[pos_] == '.')) {
        if (input_[pos_] == '.') {
            if (hasDot) break;
            hasDot = true;
        }
        pos_++;
    }

    // Support scientific notation: e.g. 1.5e10, 2E-3
    if (pos_ < static_cast<int>(input_.size()) &&
        (input_[pos_] == 'e' || input_[pos_] == 'E')) {
        pos_++;
        if (pos_ < static_cast<int>(input_.size()) &&
            (input_[pos_] == '+' || input_[pos_] == '-')) {
            pos_++;
        }
        if (pos_ >= static_cast<int>(input_.size()) || !std::isdigit(input_[pos_])) {
            throw LexerError("Invalid scientific notation at position " + std::to_string(start), start);
        }
        while (pos_ < static_cast<int>(input_.size()) && std::isdigit(input_[pos_])) {
            pos_++;
        }
    }

    return Token(TokenType::Number, input_.substr(start, pos_ - start), start);
}

Token Lexer::readIdentifier() {
    int start = pos_;
    while (pos_ < static_cast<int>(input_.size()) &&
           (std::isalnum(input_[pos_]) || input_[pos_] == '_')) {
        pos_++;
    }
    return Token(TokenType::Identifier, input_.substr(start, pos_ - start), start);
}

Token Lexer::readHistoryRef() {
    int start = pos_;
    pos_++; // skip '$'

    if (pos_ >= static_cast<int>(input_.size()) || !std::isdigit(input_[pos_])) {
        throw LexerError("Expected number after '$' at position " + std::to_string(start), start);
    }

    while (pos_ < static_cast<int>(input_.size()) && std::isdigit(input_[pos_])) {
        pos_++;
    }

    return Token(TokenType::HistoryRef, input_.substr(start, pos_ - start), start);
}

std::vector<Token> Lexer::tokenize() {
    std::vector<Token> tokens;

    while (true) {
        skipWhitespace();
        if (pos_ >= static_cast<int>(input_.size())) {
            tokens.emplace_back(TokenType::End, "", pos_);
            break;
        }

        char c = peek();
        int startPos = pos_;

        if (std::isdigit(c) || (c == '.' && pos_ + 1 < static_cast<int>(input_.size()) && std::isdigit(input_[pos_ + 1]))) {
            tokens.push_back(readNumber());
        } else if (std::isalpha(c) || c == '_') {
            tokens.push_back(readIdentifier());
        } else if (c == '$') {
            tokens.push_back(readHistoryRef());
        } else {
            pos_++;
            switch (c) {
                case '+': tokens.emplace_back(TokenType::Plus,    "+", startPos); break;
                case '-': tokens.emplace_back(TokenType::Minus,   "-", startPos); break;
                case '*': tokens.emplace_back(TokenType::Star,    "*", startPos); break;
                case '/': tokens.emplace_back(TokenType::Slash,   "/", startPos); break;
                case '%': tokens.emplace_back(TokenType::Percent, "%", startPos); break;
                case '^': tokens.emplace_back(TokenType::Caret,   "^", startPos); break;
                case '(': tokens.emplace_back(TokenType::LParen,  "(", startPos); break;
                case ')': tokens.emplace_back(TokenType::RParen,  ")", startPos); break;
                case ',': tokens.emplace_back(TokenType::Comma,   ",", startPos); break;
                case '=': tokens.emplace_back(TokenType::Equals,  "=", startPos); break;
                default: {
                    std::ostringstream oss;
                    oss << "Unexpected character '" << c << "' at position " << startPos;
                    throw LexerError(oss.str(), startPos);
                }
            }
        }
    }

    return tokens;
}

} // namespace calculator
