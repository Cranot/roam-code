#include "calculator/parser.h"

#include <cctype>
#include <cstdlib>
#include <string>
#include <utility>

#include "calculator/errors.h"

namespace calculator {

namespace {

std::string tokenForMessage(const Token& token) {
    if (!token.lexeme.empty()) {
        return token.lexeme;
    }
    return "<end>";
}

bool looksLikeIntegerLiteral(const std::string& lexeme) {
    for (const char ch : lexeme) {
        if (ch == '.' || ch == 'e' || ch == 'E') {
            return false;
        }
    }
    return true;
}

}  // namespace

Parser::Parser(std::vector<Token> tokens) : tokens_(std::move(tokens)), current_(0) {}

ExprPtr Parser::parse() {
    ExprPtr expression = parseAssignment();
    if (!isAtEnd()) {
        const Token& token = peek();
        throw ParseError("Unexpected token '" + tokenForMessage(token) + "' at position " +
                         std::to_string(token.position));
    }
    return expression;
}

ExprPtr Parser::parseAssignment() {
    ExprPtr expression = parseAdditive();
    if (!match(TokenType::Assign)) {
        return expression;
    }

    const Token& assign = previous();
    auto* variable = dynamic_cast<VariableExpr*>(expression.get());
    if (variable == nullptr) {
        throw ParseError("Invalid assignment target at position " + std::to_string(assign.position));
    }

    ExprPtr value = parseAssignment();
    return std::make_unique<AssignExpr>(variable->name, std::move(value), assign.position);
}

ExprPtr Parser::parseAdditive() {
    ExprPtr expression = parseMultiplicative();

    while (match(TokenType::Plus) || match(TokenType::Minus)) {
        const Token operator_token = previous();
        ExprPtr right = parseMultiplicative();
        expression = std::make_unique<BinaryExpr>(operator_token.type, std::move(expression), std::move(right),
                                                  operator_token.position);
    }

    return expression;
}

ExprPtr Parser::parseMultiplicative() {
    ExprPtr expression = parsePower();

    while (match(TokenType::Star) || match(TokenType::Slash) || match(TokenType::Percent)) {
        const Token operator_token = previous();
        ExprPtr right = parsePower();
        expression = std::make_unique<BinaryExpr>(operator_token.type, std::move(expression), std::move(right),
                                                  operator_token.position);
    }

    return expression;
}

ExprPtr Parser::parsePower() {
    ExprPtr expression = parseUnary();
    if (!match(TokenType::Caret)) {
        return expression;
    }

    const Token operator_token = previous();
    ExprPtr right = parsePower();
    return std::make_unique<BinaryExpr>(TokenType::Caret, std::move(expression), std::move(right),
                                        operator_token.position);
}

ExprPtr Parser::parseUnary() {
    if (!match(TokenType::Minus)) {
        return parsePrimary();
    }

    const Token operator_token = previous();
    ExprPtr right = parseUnary();
    return std::make_unique<UnaryExpr>(TokenType::Minus, std::move(right), operator_token.position);
}

ExprPtr Parser::parsePrimary() {
    if (match(TokenType::Number)) {
        const Token& number_token = previous();
        try {
            if (looksLikeIntegerLiteral(number_token.lexeme)) {
                return std::make_unique<NumberExpr>(Value::fromInteger(std::stoll(number_token.lexeme)),
                                                    number_token.position);
            }
            return std::make_unique<NumberExpr>(Value::fromDouble(std::stod(number_token.lexeme)),
                                                number_token.position);
        } catch (const std::exception&) {
            throw ParseError("Invalid number '" + number_token.lexeme + "' at position " +
                             std::to_string(number_token.position));
        }
    }

    if (match(TokenType::HistoryRef)) {
        const Token& token = previous();
        try {
            const std::size_t index = static_cast<std::size_t>(std::stoull(token.lexeme.substr(1)));
            if (index == 0) {
                throw ParseError("History references are 1-based at position " + std::to_string(token.position));
            }
            return std::make_unique<HistoryExpr>(index, token.position);
        } catch (const ParseError&) {
            throw;
        } catch (const std::exception&) {
            throw ParseError("Invalid history reference '" + token.lexeme + "' at position " +
                             std::to_string(token.position));
        }
    }

    if (match(TokenType::Identifier)) {
        const Token& identifier = previous();
        if (!match(TokenType::LParen)) {
            return std::make_unique<VariableExpr>(identifier.lexeme, identifier.position);
        }

        std::vector<ExprPtr> arguments;
        if (!check(TokenType::RParen)) {
            do {
                arguments.push_back(parseAssignment());
            } while (match(TokenType::Comma));
        }
        expect(TokenType::RParen, "Expected ')' after function arguments");
        return std::make_unique<CallExpr>(identifier.lexeme, std::move(arguments), identifier.position);
    }

    if (match(TokenType::LParen)) {
        ExprPtr expression = parseAssignment();
        expect(TokenType::RParen, "Expected ')' after expression");
        return expression;
    }

    const Token& token = peek();
    throw ParseError("Unexpected token '" + tokenForMessage(token) + "' at position " +
                     std::to_string(token.position));
}

bool Parser::match(TokenType type) {
    if (!check(type)) {
        return false;
    }
    advance();
    return true;
}

bool Parser::check(TokenType type) const {
    if (isAtEnd()) {
        return type == TokenType::EndOfInput;
    }
    return peek().type == type;
}

const Token& Parser::advance() {
    if (!isAtEnd()) {
        ++current_;
    }
    return previous();
}

const Token& Parser::peek() const {
    return tokens_[current_];
}

const Token& Parser::previous() const {
    return tokens_[current_ - 1];
}

bool Parser::isAtEnd() const {
    return peek().type == TokenType::EndOfInput;
}

const Token& Parser::expect(TokenType type, const std::string& message) {
    if (check(type)) {
        return advance();
    }

    const Token& token = peek();
    throw ParseError(message + ". Unexpected token '" + tokenForMessage(token) + "' at position " +
                     std::to_string(token.position));
}

}  // namespace calculator