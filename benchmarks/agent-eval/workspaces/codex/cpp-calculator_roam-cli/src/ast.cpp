#include "calculator/ast.h"

namespace calculator {

Expr::Expr(std::size_t expression_position) : position(expression_position) {}

NumberExpr::NumberExpr(Value numeric_value, std::size_t expression_position)
    : Expr(expression_position), value(std::move(numeric_value)) {}

VariableExpr::VariableExpr(std::string variable_name, std::size_t expression_position)
    : Expr(expression_position), name(std::move(variable_name)) {}

HistoryExpr::HistoryExpr(std::size_t history_index, std::size_t expression_position)
    : Expr(expression_position), index(history_index) {}

UnaryExpr::UnaryExpr(TokenType operator_type, ExprPtr operand, std::size_t expression_position)
    : Expr(expression_position), op(operator_type), right(std::move(operand)) {}

BinaryExpr::BinaryExpr(TokenType operator_type,
                       ExprPtr left_operand,
                       ExprPtr right_operand,
                       std::size_t expression_position)
    : Expr(expression_position), op(operator_type), left(std::move(left_operand)), right(std::move(right_operand)) {}

CallExpr::CallExpr(std::string function_name, std::vector<ExprPtr> call_arguments, std::size_t expression_position)
    : Expr(expression_position), function(std::move(function_name)), args(std::move(call_arguments)) {}

AssignExpr::AssignExpr(std::string variable_name, ExprPtr assigned_value, std::size_t expression_position)
    : Expr(expression_position), name(std::move(variable_name)), value(std::move(assigned_value)) {}

}  // namespace calculator