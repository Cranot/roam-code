#ifndef AST_H
#define AST_H

#include <memory>
#include <string>
#include <vector>

// Forward declaration
class Evaluator;

// Base AST Node
class ASTNode {
public:
    virtual ~ASTNode() = default;
    virtual double evaluate(Evaluator& evaluator) const = 0;
};

// Number Node
class NumberNode : public ASTNode {
private:
    double value;

public:
    explicit NumberNode(double value);
    double evaluate(Evaluator& evaluator) const override;
};

// Binary Operation Node
class BinaryOpNode : public ASTNode {
private:
    std::unique_ptr<ASTNode> left;
    std::unique_ptr<ASTNode> right;
    char op;

public:
    BinaryOpNode(std::unique_ptr<ASTNode> left, char op, std::unique_ptr<ASTNode> right);
    double evaluate(Evaluator& evaluator) const override;
};

// Unary Operation Node
class UnaryOpNode : public ASTNode {
private:
    std::unique_ptr<ASTNode> operand;
    char op;

public:
    UnaryOpNode(char op, std::unique_ptr<ASTNode> operand);
    double evaluate(Evaluator& evaluator) const override;
};

// Variable Node
class VariableNode : public ASTNode {
private:
    std::string name;

public:
    explicit VariableNode(const std::string& name);
    double evaluate(Evaluator& evaluator) const override;
    const std::string& getName() const { return name; }
};

// Function Call Node
class FunctionCallNode : public ASTNode {
private:
    std::string name;
    std::vector<std::unique_ptr<ASTNode>> arguments;

public:
    FunctionCallNode(const std::string& name, std::vector<std::unique_ptr<ASTNode>> arguments);
    double evaluate(Evaluator& evaluator) const override;
};

// Assignment Node
class AssignmentNode : public ASTNode {
private:
    std::string variable_name;
    std::unique_ptr<ASTNode> expression;

public:
    AssignmentNode(const std::string& variable_name, std::unique_ptr<ASTNode> expression);
    double evaluate(Evaluator& evaluator) const override;
};

#endif // AST_H
