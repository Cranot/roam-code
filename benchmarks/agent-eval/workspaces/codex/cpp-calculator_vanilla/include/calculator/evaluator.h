#pragma once

#include "calculator/ast.h"
#include "calculator/context.h"

namespace calc {

class Evaluator {
 public:
  double evaluate(const Expr& expr, EvaluationContext& context) const;

 private:
  double evaluate_expr(const Expr& expr, EvaluationContext& context) const;
  double evaluate_unary(const UnaryExpr& expr, EvaluationContext& context) const;
  double evaluate_binary(const BinaryExpr& expr, EvaluationContext& context) const;
  double evaluate_call(const CallExpr& expr, EvaluationContext& context) const;
};

}  // namespace calc