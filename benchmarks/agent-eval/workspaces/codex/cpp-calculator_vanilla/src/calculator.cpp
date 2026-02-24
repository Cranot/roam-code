#include "calculator/calculator.h"

#include "calculator/parser.h"

double calc::Calculator::evaluate(const std::string& input) {
  Parser parser(input);
  ExprPtr expression = parser.parse();
  double result = evaluator_.evaluate(*expression, context_);
  context_.push_history(result);
  return result;
}

const calc::EvaluationContext& calc::Calculator::context() const noexcept {
  return context_;
}