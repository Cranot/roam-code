#include "calculator/token.h"

namespace calculator {

std::string tokenTypeToString(TokenType type) {
    switch (type) {
        case TokenType::EndOfInput:
            return "<end>";
        case TokenType::Number:
            return "number";
        case TokenType::Identifier:
            return "identifier";
        case TokenType::HistoryRef:
            return "history reference";
        case TokenType::Plus:
            return "+";
        case TokenType::Minus:
            return "-";
        case TokenType::Star:
            return "*";
        case TokenType::Slash:
            return "/";
        case TokenType::Percent:
            return "%";
        case TokenType::Caret:
            return "^";
        case TokenType::LParen:
            return "(";
        case TokenType::RParen:
            return ")";
        case TokenType::Comma:
            return ",";
        case TokenType::Assign:
            return "=";
    }
    return "<unknown>";
}

}  // namespace calculator