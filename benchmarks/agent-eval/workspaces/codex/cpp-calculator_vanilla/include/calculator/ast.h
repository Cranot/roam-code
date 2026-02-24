#pragma once

#include <memory>
#include <string>
#include <vector>

namespace calc {

enum class UnaryOp {
  Negate,
};

enum class BinaryOp {
  Add,
  Subtract,
  Multiply,
  Divide,
  Modulo,
  Power,
};

struct Expr {
  virtual ~Expr() = default;
};

using ExprPtr = std::unique_ptr<Expr>;

struct NumberExpr : Expr {
  explicit NumberExpr(double value_in) : value(value_in) {}

  double value;
};

struct VariableExpr : Expr {
  explicit VariableExpr(std::string name_in) : name(std::move(name_in)) {}

  std::string name;
};

struct HistoryExpr : Expr {
  explicit HistoryExpr(std::size_t index_in) : index(index_in) {}

  std::size_t index;
};

struct UnaryExpr : Expr {
  UnaryExpr(UnaryOp op_in, ExprPtr operand_in)
      : op(op_in), operand(std::move(operand_in)) {}

  UnaryOp op;
  ExprPtr operand;
};

struct BinaryExpr : Expr {
  BinaryExpr(BinaryOp op_in, ExprPtr left_in, ExprPtr right_in)
      : op(op_in), left(std::move(left_in)), right(std::move(right_in)) {}

  BinaryOp op;
  ExprPtr left;
  ExprPtr right;
};

struct AssignExpr : Expr {
  AssignExpr(std::string name_in, ExprPtr value_in)
      : name(std::move(name_in)), value(std::move(value_in)) {}

  std::string name;
  ExprPtr value;
};

struct CallExpr : Expr {
  CallExpr(std::string function_in, std::vector<ExprPtr> args_in)
      : function(std::move(function_in)), args(std::move(args_in)) {}

  std::string function;
  std::vector<ExprPtr> args;
};

}  // namespace calc