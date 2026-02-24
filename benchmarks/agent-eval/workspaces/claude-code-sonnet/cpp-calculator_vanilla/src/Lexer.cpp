#include "Lexer.h"
#include <cctype>
#include <stdexcept>

Lexer::Lexer(const std::string& input)
    : input(input), position(0), current_char(input.empty() ? '\0' : input[0]) {}

void Lexer::advance() {
    position++;
    if (position < input.length()) {
        current_char = input[position];
    } else {
        current_char = '\0';
    }
}

char Lexer::peek() const {
    size_t next_pos = position + 1;
    if (next_pos < input.length()) {
        return input[next_pos];
    }
    return '\0';
}

void Lexer::skip_whitespace() {
    while (current_char != '\0' && std::isspace(current_char)) {
        advance();
    }
}

Token Lexer::number() {
    size_t start_pos = position;
    std::string num_str;

    while (current_char != '\0' && (std::isdigit(current_char) || current_char == '.')) {
        num_str += current_char;
        advance();
    }

    return Token(TokenType::NUMBER, num_str, start_pos);
}

Token Lexer::identifier() {
    size_t start_pos = position;
    std::string id_str;

    while (current_char != '\0' && (std::isalnum(current_char) || current_char == '_' || current_char == '$')) {
        id_str += current_char;
        advance();
    }

    return Token(TokenType::IDENTIFIER, id_str, start_pos);
}

Token Lexer::next_token() {
    while (current_char != '\0') {
        if (std::isspace(current_char)) {
            skip_whitespace();
            continue;
        }

        size_t current_pos = position;

        if (std::isdigit(current_char) || (current_char == '.' && std::isdigit(peek()))) {
            return number();
        }

        if (std::isalpha(current_char) || current_char == '_' || current_char == '$') {
            return identifier();
        }

        if (current_char == '+') {
            advance();
            return Token(TokenType::PLUS, "+", current_pos);
        }

        if (current_char == '-') {
            advance();
            return Token(TokenType::MINUS, "-", current_pos);
        }

        if (current_char == '*') {
            advance();
            return Token(TokenType::MULTIPLY, "*", current_pos);
        }

        if (current_char == '/') {
            advance();
            return Token(TokenType::DIVIDE, "/", current_pos);
        }

        if (current_char == '%') {
            advance();
            return Token(TokenType::MODULO, "%", current_pos);
        }

        if (current_char == '^') {
            advance();
            return Token(TokenType::POWER, "^", current_pos);
        }

        if (current_char == '(') {
            advance();
            return Token(TokenType::LPAREN, "(", current_pos);
        }

        if (current_char == ')') {
            advance();
            return Token(TokenType::RPAREN, ")", current_pos);
        }

        if (current_char == ',') {
            advance();
            return Token(TokenType::COMMA, ",", current_pos);
        }

        if (current_char == '=') {
            advance();
            return Token(TokenType::ASSIGN, "=", current_pos);
        }

        // Invalid character
        std::string invalid_char(1, current_char);
        advance();
        return Token(TokenType::INVALID, invalid_char, current_pos);
    }

    return Token(TokenType::END, "", position);
}

std::vector<Token> Lexer::tokenize() {
    std::vector<Token> tokens;
    Token token = next_token();

    while (token.type != TokenType::END) {
        tokens.push_back(token);
        token = next_token();
    }

    tokens.push_back(token); // Add END token
    return tokens;
}
