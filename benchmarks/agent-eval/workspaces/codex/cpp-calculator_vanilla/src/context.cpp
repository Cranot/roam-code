#include "calculator/context.h"

#include <cmath>

namespace calc {

EvaluationContext::EvaluationContext() {
  variables_.emplace("pi", std::acos(-1.0));
  variables_.emplace("e", std::exp(1.0));
  constants_.emplace("pi");
  constants_.emplace("e");
}

bool EvaluationContext::has_variable(const std::string& name) const {
  return variables_.find(name) != variables_.end();
}

double EvaluationContext::get_variable(const std::string& name) const {
  auto it = variables_.find(name);
  if (it == variables_.end()) {
    throw EvalError("Unknown variable '" + name + "'");
  }
  return it->second;
}

void EvaluationContext::set_variable(const std::string& name, double value) {
  if (constants_.find(name) != constants_.end()) {
    throw EvalError("Cannot assign to constant '" + name + "'");
  }

  variables_[name] = value;
}

void EvaluationContext::push_history(double value) { history_.push_back(value); }

double EvaluationContext::get_history(std::size_t index) const {
  if (index == 0 || index > history_.size()) {
    throw EvalError("History reference '$" + std::to_string(index) +
                    "' is out of range");
  }
  return history_[index - 1];
}

const std::vector<double>& EvaluationContext::history() const noexcept {
  return history_;
}

}  // namespace calc