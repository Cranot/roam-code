#include "AST.h"
#include "Evaluator.h"
#include <cmath>

// NumberNode implementation
NumberNode::NumberNode(double value) : value(value) {}

double NumberNode::evaluate(Evaluator& evaluator) const {
    return value;
}

// BinaryOpNode implementation
BinaryOpNode::BinaryOpNode(std::unique_ptr<ASTNode> left, char op, std::unique_ptr<ASTNode> right)
    : left(std::move(left)), op(op), right(std::move(right)) {}

double BinaryOpNode::evaluate(Evaluator& evaluator) const {
    double left_val = left->evaluate(evaluator);
    double right_val = right->evaluate(evaluator);

    switch (op) {
        case '+':
            return left_val + right_val;
        case '-':
            return left_val - right_val;
        case '*':
            return left_val * right_val;
        case '/':
            if (right_val == 0.0) {
                throw EvaluationError("Division by zero");
            }
            return left_val / right_val;
        case '%':
            if (right_val == 0.0) {
                throw EvaluationError("Modulo by zero");
            }
            return std::fmod(left_val, right_val);
        case '^':
            return std::pow(left_val, right_val);
        default:
            throw EvaluationError("Unknown binary operator: " + std::string(1, op));
    }
}

// UnaryOpNode implementation
UnaryOpNode::UnaryOpNode(char op, std::unique_ptr<ASTNode> operand)
    : op(op), operand(std::move(operand)) {}

double UnaryOpNode::evaluate(Evaluator& evaluator) const {
    double operand_val = operand->evaluate(evaluator);

    switch (op) {
        case '+':
            return operand_val;
        case '-':
            return -operand_val;
        default:
            throw EvaluationError("Unknown unary operator: " + std::string(1, op));
    }
}

// VariableNode implementation
VariableNode::VariableNode(const std::string& name) : name(name) {}

double VariableNode::evaluate(Evaluator& evaluator) const {
    return evaluator.get_variable(name);
}

// FunctionCallNode implementation
FunctionCallNode::FunctionCallNode(const std::string& name, std::vector<std::unique_ptr<ASTNode>> arguments)
    : name(name), arguments(std::move(arguments)) {}

double FunctionCallNode::evaluate(Evaluator& evaluator) const {
    std::vector<double> arg_values;
    for (const auto& arg : arguments) {
        arg_values.push_back(arg->evaluate(evaluator));
    }
    return evaluator.call_function(name, arg_values);
}

// AssignmentNode implementation
AssignmentNode::AssignmentNode(const std::string& variable_name, std::unique_ptr<ASTNode> expression)
    : variable_name(variable_name), expression(std::move(expression)) {}

double AssignmentNode::evaluate(Evaluator& evaluator) const {
    double value = expression->evaluate(evaluator);
    evaluator.set_variable(variable_name, value);
    return value;
}
