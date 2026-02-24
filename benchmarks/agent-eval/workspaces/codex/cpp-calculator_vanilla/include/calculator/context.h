#pragma once

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "calculator/errors.h"

namespace calc {

class EvaluationContext {
 public:
  EvaluationContext();

  bool has_variable(const std::string& name) const;
  double get_variable(const std::string& name) const;
  void set_variable(const std::string& name, double value);

  void push_history(double value);
  double get_history(std::size_t index) const;
  const std::vector<double>& history() const noexcept;

 private:
  std::unordered_map<std::string, double> variables_;
  std::unordered_set<std::string> constants_;
  std::vector<double> history_;
};

}  // namespace calc