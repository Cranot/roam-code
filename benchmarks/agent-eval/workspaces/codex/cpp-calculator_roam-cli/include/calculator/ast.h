#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <vector>

#include "calculator/token.h"
#include "calculator/value.h"

namespace calculator {

struct Expr {
    explicit Expr(std::size_t expression_position);
    virtual ~Expr() = default;

    std::size_t position;
};

using ExprPtr = std::unique_ptr<Expr>;

struct NumberExpr final : Expr {
    NumberExpr(Value numeric_value, std::size_t expression_position);
    Value value;
};

struct VariableExpr final : Expr {
    VariableExpr(std::string variable_name, std::size_t expression_position);
    std::string name;
};

struct HistoryExpr final : Expr {
    HistoryExpr(std::size_t history_index, std::size_t expression_position);
    std::size_t index;
};

struct UnaryExpr final : Expr {
    UnaryExpr(TokenType operator_type, ExprPtr operand, std::size_t expression_position);
    TokenType op;
    ExprPtr right;
};

struct BinaryExpr final : Expr {
    BinaryExpr(TokenType operator_type, ExprPtr left_operand, ExprPtr right_operand, std::size_t expression_position);
    TokenType op;
    ExprPtr left;
    ExprPtr right;
};

struct CallExpr final : Expr {
    CallExpr(std::string function_name, std::vector<ExprPtr> call_arguments, std::size_t expression_position);
    std::string function;
    std::vector<ExprPtr> args;
};

struct AssignExpr final : Expr {
    AssignExpr(std::string variable_name, ExprPtr assigned_value, std::size_t expression_position);
    std::string name;
    ExprPtr value;
};

}  // namespace calculator