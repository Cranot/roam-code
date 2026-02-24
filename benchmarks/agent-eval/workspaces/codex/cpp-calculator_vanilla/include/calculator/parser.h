#pragma once

#include <string_view>
#include <vector>

#include "calculator/ast.h"
#include "calculator/token.h"

namespace calc {

class Parser {
 public:
  explicit Parser(std::string_view input);

  ExprPtr parse();

 private:
  ExprPtr parse_assignment();
  ExprPtr parse_additive();
  ExprPtr parse_multiplicative();
  ExprPtr parse_power();
  ExprPtr parse_unary();
  ExprPtr parse_primary();

  bool match(TokenType type);
  const Token& advance();
  bool check(TokenType type) const;
  bool check_next(TokenType type) const;
  const Token& current() const;
  const Token& previous() const;
  const Token& expect(TokenType type);

  [[noreturn]] void throw_unexpected_token(const Token& token) const;

  std::vector<Token> tokens_;
  std::size_t current_index_ = 0;
};

}  // namespace calc