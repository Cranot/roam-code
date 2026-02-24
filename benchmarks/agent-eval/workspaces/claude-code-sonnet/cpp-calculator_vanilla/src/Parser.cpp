#include "Parser.h"
#include <sstream>

ParseError::ParseError(const std::string& message, size_t position)
    : std::runtime_error(message), position(position) {}

Parser::Parser(const std::vector<Token>& tokens)
    : tokens(tokens), current_index(0) {}

Token& Parser::current_token() {
    if (current_index >= tokens.size()) {
        return tokens.back();
    }
    return tokens[current_index];
}

Token& Parser::peek(int offset) {
    size_t peek_index = current_index + offset;
    if (peek_index >= tokens.size()) {
        return tokens.back();
    }
    return tokens[peek_index];
}

void Parser::advance() {
    if (current_index < tokens.size() - 1) {
        current_index++;
    }
}

void Parser::expect(TokenType type) {
    if (current_token().type != type) {
        std::ostringstream oss;
        oss << "Expected token type " << static_cast<int>(type)
            << " but got " << current_token().typeToString();
        throw ParseError(oss.str(), current_token().position);
    }
    advance();
}

std::unique_ptr<ASTNode> Parser::parse() {
    if (current_token().type == TokenType::END) {
        throw ParseError("Empty expression", 0);
    }
    auto result = parse_expression();
    if (current_token().type != TokenType::END) {
        std::ostringstream oss;
        oss << "Unexpected token '" << current_token().value
            << "' at position " << current_token().position;
        throw ParseError(oss.str(), current_token().position);
    }
    return result;
}

std::unique_ptr<ASTNode> Parser::parse_expression() {
    return parse_assignment();
}

std::unique_ptr<ASTNode> Parser::parse_assignment() {
    // Check if this is an assignment: IDENTIFIER = expression
    if (current_token().type == TokenType::IDENTIFIER && peek().type == TokenType::ASSIGN) {
        std::string var_name = current_token().value;
        advance(); // consume identifier
        advance(); // consume '='
        auto expr = parse_additive();
        return std::make_unique<AssignmentNode>(var_name, std::move(expr));
    }
    return parse_additive();
}

std::unique_ptr<ASTNode> Parser::parse_additive() {
    auto left = parse_multiplicative();

    while (current_token().type == TokenType::PLUS || current_token().type == TokenType::MINUS) {
        char op = current_token().value[0];
        advance();
        auto right = parse_multiplicative();
        left = std::make_unique<BinaryOpNode>(std::move(left), op, std::move(right));
    }

    return left;
}

std::unique_ptr<ASTNode> Parser::parse_multiplicative() {
    auto left = parse_power();

    while (current_token().type == TokenType::MULTIPLY ||
           current_token().type == TokenType::DIVIDE ||
           current_token().type == TokenType::MODULO) {
        char op = current_token().value[0];
        advance();
        auto right = parse_power();
        left = std::make_unique<BinaryOpNode>(std::move(left), op, std::move(right));
    }

    return left;
}

std::unique_ptr<ASTNode> Parser::parse_power() {
    auto left = parse_unary();

    // Right-associative: 2^3^4 = 2^(3^4)
    if (current_token().type == TokenType::POWER) {
        advance();
        auto right = parse_power(); // Recursive for right-associativity
        left = std::make_unique<BinaryOpNode>(std::move(left), '^', std::move(right));
    }

    return left;
}

std::unique_ptr<ASTNode> Parser::parse_unary() {
    if (current_token().type == TokenType::PLUS || current_token().type == TokenType::MINUS) {
        char op = current_token().value[0];
        advance();
        auto operand = parse_unary(); // Allow multiple unary operators
        return std::make_unique<UnaryOpNode>(op, std::move(operand));
    }

    return parse_primary();
}

std::unique_ptr<ASTNode> Parser::parse_primary() {
    Token& token = current_token();

    // Number
    if (token.type == TokenType::NUMBER) {
        double value = std::stod(token.value);
        advance();
        return std::make_unique<NumberNode>(value);
    }

    // Parenthesized expression
    if (token.type == TokenType::LPAREN) {
        advance();
        auto expr = parse_additive();
        expect(TokenType::RPAREN);
        return expr;
    }

    // Identifier (variable or function call)
    if (token.type == TokenType::IDENTIFIER) {
        std::string name = token.value;
        advance();

        // Check if it's a function call
        if (current_token().type == TokenType::LPAREN) {
            return parse_function_call(name);
        }

        // It's a variable
        return std::make_unique<VariableNode>(name);
    }

    std::ostringstream oss;
    oss << "Unexpected token '" << token.value << "' at position " << token.position;
    throw ParseError(oss.str(), token.position);
}

std::unique_ptr<ASTNode> Parser::parse_function_call(const std::string& name) {
    expect(TokenType::LPAREN);

    std::vector<std::unique_ptr<ASTNode>> arguments;

    // Empty argument list
    if (current_token().type == TokenType::RPAREN) {
        advance();
        return std::make_unique<FunctionCallNode>(name, std::move(arguments));
    }

    // Parse arguments
    arguments.push_back(parse_additive());

    while (current_token().type == TokenType::COMMA) {
        advance();
        arguments.push_back(parse_additive());
    }

    expect(TokenType::RPAREN);
    return std::make_unique<FunctionCallNode>(name, std::move(arguments));
}
