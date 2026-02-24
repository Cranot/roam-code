#pragma once

#include <string>

#include "calculator/context.h"
#include "calculator/evaluator.h"

namespace calc {

class Calculator {
 public:
  double evaluate(const std::string& input);

  const EvaluationContext& context() const noexcept;

 private:
  EvaluationContext context_;
  Evaluator evaluator_;
};

}  // namespace calc