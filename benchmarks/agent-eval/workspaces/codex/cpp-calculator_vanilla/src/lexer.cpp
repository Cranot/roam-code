#include "calculator/lexer.h"

#include <cctype>
#include <string>

#include "calculator/errors.h"

namespace calc {
namespace {

std::string unexpected_token_message(const std::string& token,
                                     std::size_t position) {
  return "Unexpected token '" + token + "' at position " +
         std::to_string(position + 1);
}

bool is_identifier_start(char c) {
  return std::isalpha(static_cast<unsigned char>(c)) != 0 || c == '_';
}

bool is_identifier_part(char c) {
  return std::isalnum(static_cast<unsigned char>(c)) != 0 || c == '_';
}

}  // namespace

Lexer::Lexer(std::string_view input) : input_(input) {}

Token Lexer::next_token() {
  skip_whitespace();

  if (is_at_end()) {
    return Token{TokenType::End, "", 0.0, position_};
  }

  const char c = current_char();
  const std::size_t token_position = position_;

  if (std::isdigit(static_cast<unsigned char>(c)) != 0 ||
      (c == '.' && std::isdigit(static_cast<unsigned char>(peek_char())) != 0)) {
    return read_number();
  }

  if (is_identifier_start(c)) {
    return read_identifier();
  }

  if (c == '$') {
    return read_history_reference();
  }

  advance();
  switch (c) {
    case '+':
      return Token{TokenType::Plus, "+", 0.0, token_position};
    case '-':
      return Token{TokenType::Minus, "-", 0.0, token_position};
    case '*':
      return Token{TokenType::Star, "*", 0.0, token_position};
    case '/':
      return Token{TokenType::Slash, "/", 0.0, token_position};
    case '%':
      return Token{TokenType::Percent, "%", 0.0, token_position};
    case '^':
      return Token{TokenType::Caret, "^", 0.0, token_position};
    case '(':
      return Token{TokenType::LParen, "(", 0.0, token_position};
    case ')':
      return Token{TokenType::RParen, ")", 0.0, token_position};
    case ',':
      return Token{TokenType::Comma, ",", 0.0, token_position};
    case '=':
      return Token{TokenType::Equal, "=", 0.0, token_position};
    default:
      throw ParseError(unexpected_token_message(std::string(1, c), token_position),
                       token_position);
  }
}

char Lexer::current_char() const {
  if (is_at_end()) {
    return '\0';
  }
  return input_[position_];
}

char Lexer::peek_char() const {
  if (position_ + 1 >= input_.size()) {
    return '\0';
  }
  return input_[position_ + 1];
}

bool Lexer::is_at_end() const { return position_ >= input_.size(); }

void Lexer::advance() {
  if (!is_at_end()) {
    ++position_;
  }
}

void Lexer::skip_whitespace() {
  while (!is_at_end() &&
         std::isspace(static_cast<unsigned char>(current_char())) != 0) {
    advance();
  }
}

Token Lexer::read_number() {
  const std::size_t start = position_;
  bool has_dot = false;

  if (current_char() == '.') {
    has_dot = true;
    advance();
  }

  while (std::isdigit(static_cast<unsigned char>(current_char())) != 0) {
    advance();
  }

  if (!has_dot && current_char() == '.') {
    has_dot = true;
    advance();
    while (std::isdigit(static_cast<unsigned char>(current_char())) != 0) {
      advance();
    }
  }

  if (current_char() == 'e' || current_char() == 'E') {
    const std::size_t exponent_pos = position_;
    advance();
    if (current_char() == '+' || current_char() == '-') {
      advance();
    }

    if (std::isdigit(static_cast<unsigned char>(current_char())) == 0) {
      throw ParseError(unexpected_token_message(std::string(1, input_[exponent_pos]),
                                                exponent_pos),
                       exponent_pos);
    }

    while (std::isdigit(static_cast<unsigned char>(current_char())) != 0) {
      advance();
    }
  }

  const std::string lexeme(input_.substr(start, position_ - start));

  try {
    const double value = std::stod(lexeme);
    return Token{TokenType::Number, lexeme, value, start};
  } catch (...) {
    throw ParseError("Invalid number '" + lexeme + "' at position " +
                         std::to_string(start + 1),
                     start);
  }
}

Token Lexer::read_identifier() {
  const std::size_t start = position_;
  advance();
  while (is_identifier_part(current_char())) {
    advance();
  }

  std::string lexeme(input_.substr(start, position_ - start));
  return Token{TokenType::Identifier, lexeme, 0.0, start};
}

Token Lexer::read_history_reference() {
  const std::size_t start = position_;
  advance();

  if (std::isdigit(static_cast<unsigned char>(current_char())) == 0) {
    throw ParseError(unexpected_token_message("$", start), start);
  }

  while (std::isdigit(static_cast<unsigned char>(current_char())) != 0) {
    advance();
  }

  std::string lexeme(input_.substr(start, position_ - start));
  return Token{TokenType::History, lexeme, 0.0, start};
}

}  // namespace calc