#include "calculator/evaluator.h"

#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>

#include "calculator/errors.h"

namespace calculator {

namespace {

void requireArgCount(const std::string& name, const std::vector<Value>& args, std::size_t expected) {
    if (args.size() != expected) {
        throw EvalError("Function '" + name + "' expects " + std::to_string(expected) +
                        " argument(s), got " + std::to_string(args.size()));
    }
}

void requireAtLeastArgs(const std::string& name, const std::vector<Value>& args, std::size_t minimum) {
    if (args.size() < minimum) {
        throw EvalError("Function '" + name + "' expects at least " + std::to_string(minimum) +
                        " argument(s), got " + std::to_string(args.size()));
    }
}

Value applyUnaryMathFunction(const std::string& name,
                             const std::vector<Value>& args,
                             const std::function<double(double)>& function) {
    requireArgCount(name, args, 1);
    return Value::fromDouble(function(args[0].asDouble()));
}

bool allIntegers(const std::vector<Value>& args) {
    return std::all_of(args.begin(), args.end(), [](const Value& value) { return value.isInteger(); });
}

}  // namespace

Value Evaluator::evaluate(const Expr& expr, EvaluationContext& context) const {
    if (const auto* node = dynamic_cast<const NumberExpr*>(&expr)) {
        return evaluateNumber(*node);
    }
    if (const auto* node = dynamic_cast<const VariableExpr*>(&expr)) {
        return evaluateVariable(*node, context);
    }
    if (const auto* node = dynamic_cast<const HistoryExpr*>(&expr)) {
        return evaluateHistory(*node, context);
    }
    if (const auto* node = dynamic_cast<const UnaryExpr*>(&expr)) {
        return evaluateUnary(*node, context);
    }
    if (const auto* node = dynamic_cast<const BinaryExpr*>(&expr)) {
        return evaluateBinary(*node, context);
    }
    if (const auto* node = dynamic_cast<const CallExpr*>(&expr)) {
        return evaluateCall(*node, context);
    }
    if (const auto* node = dynamic_cast<const AssignExpr*>(&expr)) {
        return evaluateAssign(*node, context);
    }

    throw EvalError("Unknown expression type");
}

Value Evaluator::evaluateNumber(const NumberExpr& expr) const {
    return expr.value;
}

Value Evaluator::evaluateVariable(const VariableExpr& expr, EvaluationContext& context) const {
    return context.getVariable(expr.name);
}

Value Evaluator::evaluateHistory(const HistoryExpr& expr, EvaluationContext& context) const {
    return context.getHistory(expr.index);
}

Value Evaluator::evaluateUnary(const UnaryExpr& expr, EvaluationContext& context) const {
    const Value right = evaluate(*expr.right, context);
    if (expr.op == TokenType::Minus) {
        if (right.isInteger()) {
            return Value::fromInteger(-right.asInteger());
        }
        return Value::fromDouble(-right.asDouble());
    }

    throw EvalError("Unsupported unary operator at position " + std::to_string(expr.position));
}

Value Evaluator::evaluateBinary(const BinaryExpr& expr, EvaluationContext& context) const {
    const Value left = evaluate(*expr.left, context);
    const Value right = evaluate(*expr.right, context);
    return applyBinaryOperator(expr.op, left, right, expr.position);
}

Value Evaluator::evaluateCall(const CallExpr& expr, EvaluationContext& context) const {
    std::vector<Value> args;
    args.reserve(expr.args.size());
    for (const ExprPtr& arg : expr.args) {
        args.push_back(evaluate(*arg, context));
    }
    return applyFunction(expr.function, args, expr.position);
}

Value Evaluator::evaluateAssign(const AssignExpr& expr, EvaluationContext& context) const {
    const Value value = evaluate(*expr.value, context);
    context.setVariable(expr.name, value);
    return value;
}

Value Evaluator::applyBinaryOperator(TokenType op, const Value& left, const Value& right, std::size_t position) const {
    const bool both_integers = left.isInteger() && right.isInteger();

    switch (op) {
        case TokenType::Plus:
            if (both_integers) {
                return Value::fromInteger(left.asInteger() + right.asInteger());
            }
            return Value::fromDouble(left.asDouble() + right.asDouble());

        case TokenType::Minus:
            if (both_integers) {
                return Value::fromInteger(left.asInteger() - right.asInteger());
            }
            return Value::fromDouble(left.asDouble() - right.asDouble());

        case TokenType::Star:
            if (both_integers) {
                return Value::fromInteger(left.asInteger() * right.asInteger());
            }
            return Value::fromDouble(left.asDouble() * right.asDouble());

        case TokenType::Slash: {
            const double denominator = right.asDouble();
            if (denominator == 0.0) {
                throw EvalError("Division by zero at position " + std::to_string(position));
            }
            if (both_integers && right.asInteger() != 0 && (left.asInteger() % right.asInteger() == 0)) {
                return Value::fromInteger(left.asInteger() / right.asInteger());
            }
            return Value::fromDouble(left.asDouble() / denominator);
        }

        case TokenType::Percent:
            if (!both_integers) {
                throw EvalError("Modulo operator '%' requires integer operands at position " +
                                std::to_string(position));
            }
            if (right.asInteger() == 0) {
                throw EvalError("Modulo by zero at position " + std::to_string(position));
            }
            return Value::fromInteger(left.asInteger() % right.asInteger());

        case TokenType::Caret:
            return Value::fromDouble(std::pow(left.asDouble(), right.asDouble()));

        default:
            break;
    }

    throw EvalError("Unsupported binary operator at position " + std::to_string(position));
}

Value Evaluator::applyFunction(const std::string& name, const std::vector<Value>& args, std::size_t position) const {
    if (name == "sin") {
        return applyUnaryMathFunction(name, args, static_cast<double (*)(double)>(std::sin));
    }
    if (name == "cos") {
        return applyUnaryMathFunction(name, args, static_cast<double (*)(double)>(std::cos));
    }
    if (name == "tan") {
        return applyUnaryMathFunction(name, args, static_cast<double (*)(double)>(std::tan));
    }
    if (name == "sqrt") {
        requireArgCount(name, args, 1);
        if (args[0].asDouble() < 0.0) {
            throw EvalError("sqrt() domain error at position " + std::to_string(position));
        }
        return Value::fromDouble(std::sqrt(args[0].asDouble()));
    }
    if (name == "log") {
        requireArgCount(name, args, 1);
        if (args[0].asDouble() <= 0.0) {
            throw EvalError("log() domain error at position " + std::to_string(position));
        }
        return Value::fromDouble(std::log(args[0].asDouble()));
    }
    if (name == "log10") {
        requireArgCount(name, args, 1);
        if (args[0].asDouble() <= 0.0) {
            throw EvalError("log10() domain error at position " + std::to_string(position));
        }
        return Value::fromDouble(std::log10(args[0].asDouble()));
    }
    if (name == "abs") {
        requireArgCount(name, args, 1);
        if (args[0].isInteger()) {
            return Value::fromInteger(std::llabs(args[0].asInteger()));
        }
        return Value::fromDouble(std::fabs(args[0].asDouble()));
    }
    if (name == "ceil") {
        requireArgCount(name, args, 1);
        return Value::fromDouble(std::ceil(args[0].asDouble()));
    }
    if (name == "floor") {
        requireArgCount(name, args, 1);
        return Value::fromDouble(std::floor(args[0].asDouble()));
    }
    if (name == "min") {
        requireAtLeastArgs(name, args, 2);
        if (allIntegers(args)) {
            long long result = args[0].asInteger();
            for (std::size_t i = 1; i < args.size(); ++i) {
                result = std::min(result, args[i].asInteger());
            }
            return Value::fromInteger(result);
        }

        double result = args[0].asDouble();
        for (std::size_t i = 1; i < args.size(); ++i) {
            result = std::min(result, args[i].asDouble());
        }
        return Value::fromDouble(result);
    }
    if (name == "max") {
        requireAtLeastArgs(name, args, 2);
        if (allIntegers(args)) {
            long long result = args[0].asInteger();
            for (std::size_t i = 1; i < args.size(); ++i) {
                result = std::max(result, args[i].asInteger());
            }
            return Value::fromInteger(result);
        }

        double result = args[0].asDouble();
        for (std::size_t i = 1; i < args.size(); ++i) {
            result = std::max(result, args[i].asDouble());
        }
        return Value::fromDouble(result);
    }

    throw EvalError("Unknown function '" + name + "'");
}

}  // namespace calculator