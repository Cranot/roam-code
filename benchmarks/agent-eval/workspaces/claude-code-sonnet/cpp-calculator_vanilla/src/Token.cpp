#include "Token.h"

Token::Token(TokenType type, const std::string& value, size_t position)
    : type(type), value(value), position(position) {}

std::string Token::typeToString() const {
    switch (type) {
        case TokenType::NUMBER: return "NUMBER";
        case TokenType::IDENTIFIER: return "IDENTIFIER";
        case TokenType::PLUS: return "PLUS";
        case TokenType::MINUS: return "MINUS";
        case TokenType::MULTIPLY: return "MULTIPLY";
        case TokenType::DIVIDE: return "DIVIDE";
        case TokenType::MODULO: return "MODULO";
        case TokenType::POWER: return "POWER";
        case TokenType::LPAREN: return "LPAREN";
        case TokenType::RPAREN: return "RPAREN";
        case TokenType::COMMA: return "COMMA";
        case TokenType::ASSIGN: return "ASSIGN";
        case TokenType::END: return "END";
        case TokenType::INVALID: return "INVALID";
        default: return "UNKNOWN";
    }
}

std::ostream& operator<<(std::ostream& os, const Token& token) {
    os << "Token(" << token.typeToString() << ", '" << token.value
       << "', pos=" << token.position << ")";
    return os;
}
