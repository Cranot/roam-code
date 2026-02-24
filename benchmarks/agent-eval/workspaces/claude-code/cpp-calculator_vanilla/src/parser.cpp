#include "calculator/parser.h"
#include <sstream>
#include <cstdlib>

namespace calculator {

Parser::Parser(const std::vector<Token>& tokens) : tokens_(tokens), pos_(0) {}

const Token& Parser::current() const {
    return tokens_[pos_];
}

const Token& Parser::peek() const {
    return tokens_[pos_];
}

const Token& Parser::advance() {
    const Token& tok = tokens_[pos_];
    if (pos_ < tokens_.size() - 1) pos_++;
    return tok;
}

bool Parser::match(TokenType type) {
    if (current().type == type) {
        advance();
        return true;
    }
    return false;
}

const Token& Parser::expect(TokenType type) {
    if (current().type != type) {
        std::ostringstream oss;
        oss << "Expected " << tokenTypeName(type)
            << " but got " << tokenTypeName(current().type);
        if (!current().value.empty()) {
            oss << " '" << current().value << "'";
        }
        oss << " at position " << current().position;
        throw ParseError(oss.str(), current().position);
    }
    return advance();
}

bool Parser::atEnd() const {
    return current().type == TokenType::End;
}

std::unique_ptr<ASTNode> Parser::parse() {
    if (atEnd()) {
        throw ParseError("Empty expression", 0);
    }
    auto node = parseExpression();
    if (!atEnd()) {
        std::ostringstream oss;
        oss << "Unexpected token '" << current().value
            << "' at position " << current().position;
        throw ParseError(oss.str(), current().position);
    }
    return node;
}

// expression := assignment
std::unique_ptr<ASTNode> Parser::parseExpression() {
    return parseAssignment();
}

// assignment := IDENTIFIER '=' expression | addSub
std::unique_ptr<ASTNode> Parser::parseAssignment() {
    // Look ahead: if we have IDENTIFIER '=', it's an assignment
    if (current().type == TokenType::Identifier && pos_ + 1 < tokens_.size() &&
        tokens_[pos_ + 1].type == TokenType::Equals) {
        std::string name = current().value;
        int namePos = current().position;
        advance(); // skip identifier
        advance(); // skip '='
        auto value = parseExpression();

        auto node = std::make_unique<ASTNode>(NodeType::Assignment);
        node->strValue = name;
        node->children.push_back(std::move(value));
        return node;
    }
    return parseAddSub();
}

// addSub := mulDivMod (('+' | '-') mulDivMod)*
std::unique_ptr<ASTNode> Parser::parseAddSub() {
    auto left = parseMulDivMod();

    while (current().type == TokenType::Plus || current().type == TokenType::Minus) {
        std::string op = current().value;
        advance();
        auto right = parseMulDivMod();

        auto node = std::make_unique<ASTNode>(NodeType::BinaryOp);
        node->op = op;
        node->children.push_back(std::move(left));
        node->children.push_back(std::move(right));
        left = std::move(node);
    }

    return left;
}

// mulDivMod := unary (('*' | '/' | '%') unary)*
std::unique_ptr<ASTNode> Parser::parseMulDivMod() {
    auto left = parseUnary();

    while (current().type == TokenType::Star || current().type == TokenType::Slash ||
           current().type == TokenType::Percent) {
        std::string op = current().value;
        advance();
        auto right = parseUnary();

        auto node = std::make_unique<ASTNode>(NodeType::BinaryOp);
        node->op = op;
        node->children.push_back(std::move(left));
        node->children.push_back(std::move(right));
        left = std::move(node);
    }

    return left;
}

// unary := '-' unary | '+' unary | power
std::unique_ptr<ASTNode> Parser::parseUnary() {
    if (current().type == TokenType::Minus) {
        advance();
        auto operand = parseUnary();

        auto node = std::make_unique<ASTNode>(NodeType::UnaryMinus);
        node->children.push_back(std::move(operand));
        return node;
    }

    // unary plus: just skip it
    if (current().type == TokenType::Plus) {
        advance();
        return parseUnary();
    }

    return parsePower();
}

// power := primary ('^' unary)?  (right-associative)
std::unique_ptr<ASTNode> Parser::parsePower() {
    auto left = parsePrimary();

    if (current().type == TokenType::Caret) {
        advance();
        auto right = parseUnary(); // right-recursive through unary for right-associativity

        auto node = std::make_unique<ASTNode>(NodeType::BinaryOp);
        node->op = "^";
        node->children.push_back(std::move(left));
        node->children.push_back(std::move(right));
        return node;
    }

    return left;
}

// primary := NUMBER | IDENTIFIER | IDENTIFIER '(' args ')' | '(' expression ')' | HISTORY_REF
std::unique_ptr<ASTNode> Parser::parsePrimary() {
    const Token& tok = current();

    if (tok.type == TokenType::Number) {
        advance();
        auto node = std::make_unique<ASTNode>(NodeType::Number);
        node->numValue = std::stod(tok.value);
        return node;
    }

    if (tok.type == TokenType::Identifier) {
        std::string name = tok.value;
        int namePos = tok.position;
        advance();

        // Check for function call
        if (current().type == TokenType::LParen) {
            return parseFunctionCall(name, namePos);
        }

        // It's a variable reference
        auto node = std::make_unique<ASTNode>(NodeType::Variable);
        node->strValue = name;
        return node;
    }

    if (tok.type == TokenType::HistoryRef) {
        advance();
        auto node = std::make_unique<ASTNode>(NodeType::HistoryRef);
        // Extract the number from $N
        node->numValue = std::stod(tok.value.substr(1));
        return node;
    }

    if (tok.type == TokenType::LParen) {
        advance();
        auto expr = parseExpression();
        expect(TokenType::RParen);
        return expr;
    }

    std::ostringstream oss;
    oss << "Unexpected token '" << tok.value << "' at position " << tok.position;
    throw ParseError(oss.str(), tok.position);
}

std::unique_ptr<ASTNode> Parser::parseFunctionCall(const std::string& name, int pos) {
    advance(); // skip '('
    auto node = std::make_unique<ASTNode>(NodeType::FunctionCall);
    node->strValue = name;

    if (current().type != TokenType::RParen) {
        node->children.push_back(parseExpression());
        while (current().type == TokenType::Comma) {
            advance();
            node->children.push_back(parseExpression());
        }
    }

    expect(TokenType::RParen);
    return node;
}

} // namespace calculator
