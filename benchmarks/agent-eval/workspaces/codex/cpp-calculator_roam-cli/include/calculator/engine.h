#pragma once

#include <string>

#include "calculator/context.h"
#include "calculator/evaluator.h"
#include "calculator/value.h"

namespace calculator {

class CalculatorEngine {
public:
    Value evaluateExpression(const std::string& expression);

    EvaluationContext& context();
    const EvaluationContext& context() const;

private:
    EvaluationContext context_;
    Evaluator evaluator_;
};

}  // namespace calculator