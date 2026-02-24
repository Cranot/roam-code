#pragma once

#include <stdexcept>
#include <string>

namespace calc {

class ParseError : public std::runtime_error {
 public:
  ParseError(const std::string& message, std::size_t position)
      : std::runtime_error(message), position_(position) {}

  std::size_t position() const noexcept { return position_; }

 private:
  std::size_t position_;
};

class EvalError : public std::runtime_error {
 public:
  explicit EvalError(const std::string& message) : std::runtime_error(message) {}
};

}  // namespace calc