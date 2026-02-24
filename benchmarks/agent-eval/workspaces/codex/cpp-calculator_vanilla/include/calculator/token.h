#pragma once

#include <cstddef>
#include <string>

namespace calc {

enum class TokenType {
  End,
  Number,
  Identifier,
  History,
  Plus,
  Minus,
  Star,
  Slash,
  Percent,
  Caret,
  LParen,
  RParen,
  Comma,
  Equal
};

struct Token {
  TokenType type = TokenType::End;
  std::string lexeme;
  double number_value = 0.0;
  std::size_t position = 0;  // 0-based character index.
};

}  // namespace calc