#include "calculator/engine.h"

#include <cctype>

#include "calculator/errors.h"
#include "calculator/lexer.h"
#include "calculator/parser.h"

namespace calculator {

namespace {

bool isBlank(const std::string& value) {
    for (char ch : value) {
        if (!std::isspace(static_cast<unsigned char>(ch))) {
            return false;
        }
    }
    return true;
}

}  // namespace

Value CalculatorEngine::evaluateExpression(const std::string& expression) {
    if (isBlank(expression)) {
        throw ParseError("Empty expression");
    }

    Lexer lexer(expression);
    Parser parser(lexer.tokenize());
    ExprPtr ast = parser.parse();

    const Value result = evaluator_.evaluate(*ast, context_);
    context_.pushHistory(result);
    return result;
}

EvaluationContext& CalculatorEngine::context() {
    return context_;
}

const EvaluationContext& CalculatorEngine::context() const {
    return context_;
}

}  // namespace calculator