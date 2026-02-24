#ifndef CALCULATOR_PARSER_H
#define CALCULATOR_PARSER_H

#include "calculator/lexer.h"
#include <memory>
#include <string>
#include <vector>
#include <stdexcept>

namespace calculator {

// AST node types
enum class NodeType {
    Number,
    UnaryMinus,
    BinaryOp,
    Variable,
    Assignment,
    FunctionCall,
    HistoryRef
};

struct ASTNode {
    NodeType type;
    double numValue = 0.0;
    std::string strValue;
    std::string op;
    std::vector<std::unique_ptr<ASTNode>> children;

    ASTNode(NodeType type) : type(type) {}
};

class ParseError : public std::runtime_error {
public:
    int position;
    ParseError(const std::string& msg, int pos)
        : std::runtime_error(msg), position(pos) {}
};

class Parser {
public:
    explicit Parser(const std::vector<Token>& tokens);
    std::unique_ptr<ASTNode> parse();

private:
    std::vector<Token> tokens_;
    size_t pos_;

    const Token& current() const;
    const Token& peek() const;
    const Token& advance();
    bool match(TokenType type);
    const Token& expect(TokenType type);
    bool atEnd() const;

    std::unique_ptr<ASTNode> parseExpression();
    std::unique_ptr<ASTNode> parseAssignment();
    std::unique_ptr<ASTNode> parseAddSub();
    std::unique_ptr<ASTNode> parseMulDivMod();
    std::unique_ptr<ASTNode> parsePower();
    std::unique_ptr<ASTNode> parseUnary();
    std::unique_ptr<ASTNode> parsePrimary();
    std::unique_ptr<ASTNode> parseFunctionCall(const std::string& name, int pos);
};

} // namespace calculator

#endif // CALCULATOR_PARSER_H
