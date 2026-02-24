#pragma once

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "calculator/value.h"

namespace calculator {

class EvaluationContext {
public:
    EvaluationContext();

    bool hasVariable(const std::string& name) const;
    const Value& getVariable(const std::string& name) const;
    void setVariable(const std::string& name, const Value& value);

    const Value& getHistory(std::size_t index) const;
    void pushHistory(const Value& value);
    std::size_t historySize() const;

private:
    std::unordered_map<std::string, Value> variables_;
    std::unordered_set<std::string> constants_;
    std::vector<Value> history_;
};

}  // namespace calculator