#pragma once

#include <string>
#include <vector>

#include "calculator/ast.h"
#include "calculator/context.h"
#include "calculator/value.h"

namespace calculator {

class Evaluator {
public:
    Value evaluate(const Expr& expr, EvaluationContext& context) const;

private:
    Value evaluateNumber(const NumberExpr& expr) const;
    Value evaluateVariable(const VariableExpr& expr, EvaluationContext& context) const;
    Value evaluateHistory(const HistoryExpr& expr, EvaluationContext& context) const;
    Value evaluateUnary(const UnaryExpr& expr, EvaluationContext& context) const;
    Value evaluateBinary(const BinaryExpr& expr, EvaluationContext& context) const;
    Value evaluateCall(const CallExpr& expr, EvaluationContext& context) const;
    Value evaluateAssign(const AssignExpr& expr, EvaluationContext& context) const;

    Value applyBinaryOperator(TokenType op, const Value& left, const Value& right, std::size_t position) const;
    Value applyFunction(const std::string& name, const std::vector<Value>& args, std::size_t position) const;
};

}  // namespace calculator