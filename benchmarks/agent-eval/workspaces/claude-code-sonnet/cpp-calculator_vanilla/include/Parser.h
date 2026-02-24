#ifndef PARSER_H
#define PARSER_H

#include "Token.h"
#include "AST.h"
#include <vector>
#include <memory>
#include <stdexcept>

class ParseError : public std::runtime_error {
private:
    size_t position;

public:
    ParseError(const std::string& message, size_t position);
    size_t getPosition() const { return position; }
};

class Parser {
private:
    std::vector<Token> tokens;
    size_t current_index;

    Token& current_token();
    Token& peek(int offset = 1);
    void advance();
    void expect(TokenType type);

    // Recursive descent parsing methods
    std::unique_ptr<ASTNode> parse_expression();
    std::unique_ptr<ASTNode> parse_assignment();
    std::unique_ptr<ASTNode> parse_additive();
    std::unique_ptr<ASTNode> parse_multiplicative();
    std::unique_ptr<ASTNode> parse_power();
    std::unique_ptr<ASTNode> parse_unary();
    std::unique_ptr<ASTNode> parse_primary();
    std::unique_ptr<ASTNode> parse_function_call(const std::string& name);

public:
    explicit Parser(const std::vector<Token>& tokens);
    std::unique_ptr<ASTNode> parse();
};

#endif // PARSER_H
