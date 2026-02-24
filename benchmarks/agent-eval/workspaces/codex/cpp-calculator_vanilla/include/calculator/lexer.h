#pragma once

#include <string_view>

#include "calculator/token.h"

namespace calc {

class Lexer {
 public:
  explicit Lexer(std::string_view input);

  Token next_token();

 private:
  char current_char() const;
  char peek_char() const;
  bool is_at_end() const;
  void advance();
  void skip_whitespace();

  Token read_number();
  Token read_identifier();
  Token read_history_reference();

  std::string_view input_;
  std::size_t position_ = 0;
};

}  // namespace calc