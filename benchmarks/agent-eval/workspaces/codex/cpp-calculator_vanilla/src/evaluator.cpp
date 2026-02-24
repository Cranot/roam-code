#include "calculator/evaluator.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <string>
#include <vector>

#include "calculator/errors.h"

namespace calc {
namespace {

bool is_zero(double value) {
  return std::fabs(value) <= std::numeric_limits<double>::epsilon();
}

bool is_integral_value(double value) {
  return std::fabs(value - std::round(value)) <= 1e-9;
}

void throw_invalid_arg_count(const std::string& function_name,
                             std::size_t expected,
                             std::size_t actual) {
  throw EvalError("Function '" + function_name + "' expects " +
                  std::to_string(expected) + " argument(s), got " +
                  std::to_string(actual));
}

void throw_invalid_min_max_count(const std::string& function_name,
                                 std::size_t actual) {
  throw EvalError("Function '" + function_name +
                  "' expects at least 2 arguments, got " +
                  std::to_string(actual));
}

}  // namespace

double Evaluator::evaluate(const Expr& expr, EvaluationContext& context) const {
  return evaluate_expr(expr, context);
}

double Evaluator::evaluate_expr(const Expr& expr,
                                EvaluationContext& context) const {
  if (const auto* number_expr = dynamic_cast<const NumberExpr*>(&expr)) {
    return number_expr->value;
  }

  if (const auto* variable_expr = dynamic_cast<const VariableExpr*>(&expr)) {
    return context.get_variable(variable_expr->name);
  }

  if (const auto* history_expr = dynamic_cast<const HistoryExpr*>(&expr)) {
    return context.get_history(history_expr->index);
  }

  if (const auto* unary_expr = dynamic_cast<const UnaryExpr*>(&expr)) {
    return evaluate_unary(*unary_expr, context);
  }

  if (const auto* binary_expr = dynamic_cast<const BinaryExpr*>(&expr)) {
    return evaluate_binary(*binary_expr, context);
  }

  if (const auto* assign_expr = dynamic_cast<const AssignExpr*>(&expr)) {
    double value = evaluate_expr(*assign_expr->value, context);
    context.set_variable(assign_expr->name, value);
    return value;
  }

  if (const auto* call_expr = dynamic_cast<const CallExpr*>(&expr)) {
    return evaluate_call(*call_expr, context);
  }

  throw EvalError("Internal evaluator error");
}

double Evaluator::evaluate_unary(const UnaryExpr& expr,
                                 EvaluationContext& context) const {
  const double operand = evaluate_expr(*expr.operand, context);

  switch (expr.op) {
    case UnaryOp::Negate:
      return -operand;
  }

  throw EvalError("Unsupported unary operator");
}

double Evaluator::evaluate_binary(const BinaryExpr& expr,
                                  EvaluationContext& context) const {
  const double left = evaluate_expr(*expr.left, context);
  const double right = evaluate_expr(*expr.right, context);

  switch (expr.op) {
    case BinaryOp::Add:
      return left + right;
    case BinaryOp::Subtract:
      return left - right;
    case BinaryOp::Multiply:
      return left * right;
    case BinaryOp::Divide:
      if (is_zero(right)) {
        throw EvalError("Division by zero");
      }
      return left / right;
    case BinaryOp::Modulo:
      if (is_zero(right)) {
        throw EvalError("Division by zero");
      }
      if (is_integral_value(left) && is_integral_value(right)) {
        const long long left_int = static_cast<long long>(std::llround(left));
        const long long right_int = static_cast<long long>(std::llround(right));
        return static_cast<double>(left_int % right_int);
      }
      return std::fmod(left, right);
    case BinaryOp::Power:
      return std::pow(left, right);
  }

  throw EvalError("Unsupported binary operator");
}

double Evaluator::evaluate_call(const CallExpr& expr,
                                EvaluationContext& context) const {
  std::vector<double> args;
  args.reserve(expr.args.size());
  for (const auto& arg : expr.args) {
    args.push_back(evaluate_expr(*arg, context));
  }

  const std::string& name = expr.function;

  if (name == "sin") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    return std::sin(args[0]);
  }

  if (name == "cos") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    return std::cos(args[0]);
  }

  if (name == "tan") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    return std::tan(args[0]);
  }

  if (name == "sqrt") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    if (args[0] < 0.0) {
      throw EvalError("sqrt() domain error: argument must be >= 0");
    }
    return std::sqrt(args[0]);
  }

  if (name == "log") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    if (args[0] <= 0.0) {
      throw EvalError("log() domain error: argument must be > 0");
    }
    return std::log(args[0]);
  }

  if (name == "log10") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    if (args[0] <= 0.0) {
      throw EvalError("log10() domain error: argument must be > 0");
    }
    return std::log10(args[0]);
  }

  if (name == "abs") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    return std::fabs(args[0]);
  }

  if (name == "ceil") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    return std::ceil(args[0]);
  }

  if (name == "floor") {
    if (args.size() != 1) {
      throw_invalid_arg_count(name, 1, args.size());
    }
    return std::floor(args[0]);
  }

  if (name == "min") {
    if (args.size() < 2) {
      throw_invalid_min_max_count(name, args.size());
    }
    return *std::min_element(args.begin(), args.end());
  }

  if (name == "max") {
    if (args.size() < 2) {
      throw_invalid_min_max_count(name, args.size());
    }
    return *std::max_element(args.begin(), args.end());
  }

  throw EvalError("Unknown function '" + name + "'");
}

}  // namespace calc