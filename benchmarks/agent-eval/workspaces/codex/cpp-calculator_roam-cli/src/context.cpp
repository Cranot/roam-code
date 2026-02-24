#include "calculator/context.h"

#include <cmath>

#include "calculator/errors.h"

namespace calculator {

EvaluationContext::EvaluationContext() {
    variables_.emplace("pi", Value::fromDouble(std::acos(-1.0)));
    variables_.emplace("e", Value::fromDouble(std::exp(1.0)));
    constants_.emplace("pi");
    constants_.emplace("e");
}

bool EvaluationContext::hasVariable(const std::string& name) const {
    return variables_.find(name) != variables_.end();
}

const Value& EvaluationContext::getVariable(const std::string& name) const {
    const auto it = variables_.find(name);
    if (it == variables_.end()) {
        throw EvalError("Unknown variable '" + name + "'");
    }
    return it->second;
}

void EvaluationContext::setVariable(const std::string& name, const Value& value) {
    if (constants_.find(name) != constants_.end()) {
        throw EvalError("Cannot assign to built-in constant '" + name + "'");
    }
    variables_[name] = value;
}

const Value& EvaluationContext::getHistory(std::size_t index) const {
    if (index == 0 || index > history_.size()) {
        throw EvalError("History reference '$" + std::to_string(index) + "' out of range");
    }
    return history_[index - 1];
}

void EvaluationContext::pushHistory(const Value& value) {
    history_.push_back(value);
}

std::size_t EvaluationContext::historySize() const {
    return history_.size();
}

}  // namespace calculator