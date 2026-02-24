#include "calculator/parser.h"

#include <stdexcept>
#include <string>
#include <utility>

#include "calculator/errors.h"
#include "calculator/lexer.h"

namespace calc {
namespace {

std::string token_display(const Token& token) {
  if (!token.lexeme.empty()) {
    return token.lexeme;
  }
  if (token.type == TokenType::End) {
    return "end of input";
  }
  return "?";
}

std::string unexpected_token_message(const Token& token) {
  return "Unexpected token '" + token_display(token) + "' at position " +
         std::to_string(token.position + 1);
}

}  // namespace

Parser::Parser(std::string_view input) {
  Lexer lexer(input);
  while (true) {
    Token token = lexer.next_token();
    tokens_.push_back(token);
    if (token.type == TokenType::End) {
      break;
    }
  }
}

ExprPtr Parser::parse() {
  ExprPtr expr = parse_assignment();
  if (!check(TokenType::End)) {
    throw_unexpected_token(current());
  }
  return expr;
}

ExprPtr Parser::parse_assignment() {
  if (check(TokenType::Identifier) && check_next(TokenType::Equal)) {
    const std::string name = current().lexeme;
    advance();
    advance();
    ExprPtr value = parse_assignment();
    return std::make_unique<AssignExpr>(name, std::move(value));
  }

  return parse_additive();
}

ExprPtr Parser::parse_additive() {
  ExprPtr expr = parse_multiplicative();

  while (true) {
    if (match(TokenType::Plus)) {
      ExprPtr right = parse_multiplicative();
      expr = std::make_unique<BinaryExpr>(BinaryOp::Add, std::move(expr),
                                          std::move(right));
      continue;
    }
    if (match(TokenType::Minus)) {
      ExprPtr right = parse_multiplicative();
      expr = std::make_unique<BinaryExpr>(BinaryOp::Subtract, std::move(expr),
                                          std::move(right));
      continue;
    }
    break;
  }

  return expr;
}

ExprPtr Parser::parse_multiplicative() {
  ExprPtr expr = parse_power();

  while (true) {
    if (match(TokenType::Star)) {
      ExprPtr right = parse_power();
      expr = std::make_unique<BinaryExpr>(BinaryOp::Multiply, std::move(expr),
                                          std::move(right));
      continue;
    }
    if (match(TokenType::Slash)) {
      ExprPtr right = parse_power();
      expr = std::make_unique<BinaryExpr>(BinaryOp::Divide, std::move(expr),
                                          std::move(right));
      continue;
    }
    if (match(TokenType::Percent)) {
      ExprPtr right = parse_power();
      expr = std::make_unique<BinaryExpr>(BinaryOp::Modulo, std::move(expr),
                                          std::move(right));
      continue;
    }
    break;
  }

  return expr;
}

ExprPtr Parser::parse_power() {
  ExprPtr left = parse_unary();

  if (match(TokenType::Caret)) {
    ExprPtr right = parse_power();
    return std::make_unique<BinaryExpr>(BinaryOp::Power, std::move(left),
                                        std::move(right));
  }

  return left;
}

ExprPtr Parser::parse_unary() {
  if (match(TokenType::Minus)) {
    ExprPtr operand = parse_unary();
    return std::make_unique<UnaryExpr>(UnaryOp::Negate, std::move(operand));
  }

  return parse_primary();
}

ExprPtr Parser::parse_primary() {
  if (match(TokenType::Number)) {
    return std::make_unique<NumberExpr>(previous().number_value);
  }

  if (match(TokenType::History)) {
    const std::string& lexeme = previous().lexeme;
    try {
      std::size_t index = static_cast<std::size_t>(
          std::stoull(lexeme.size() > 1 ? lexeme.substr(1) : ""));
      return std::make_unique<HistoryExpr>(index);
    } catch (...) {
      throw ParseError("Invalid history reference '" + lexeme + "' at position " +
                           std::to_string(previous().position + 1),
                       previous().position);
    }
  }

  if (match(TokenType::Identifier)) {
    std::string name = previous().lexeme;

    if (match(TokenType::LParen)) {
      std::vector<ExprPtr> args;

      if (!check(TokenType::RParen)) {
        do {
          args.push_back(parse_assignment());
        } while (match(TokenType::Comma));
      }

      expect(TokenType::RParen);
      return std::make_unique<CallExpr>(std::move(name), std::move(args));
    }

    return std::make_unique<VariableExpr>(std::move(name));
  }

  if (match(TokenType::LParen)) {
    ExprPtr expr = parse_assignment();
    expect(TokenType::RParen);
    return expr;
  }

  throw_unexpected_token(current());
}

bool Parser::match(TokenType type) {
  if (!check(type)) {
    return false;
  }
  advance();
  return true;
}

const Token& Parser::advance() {
  if (current_index_ < tokens_.size()) {
    ++current_index_;
  }
  return previous();
}

bool Parser::check(TokenType type) const {
  return current().type == type;
}

bool Parser::check_next(TokenType type) const {
  if (current_index_ + 1 >= tokens_.size()) {
    return false;
  }
  return tokens_[current_index_ + 1].type == type;
}

const Token& Parser::current() const {
  if (current_index_ >= tokens_.size()) {
    return tokens_.back();
  }
  return tokens_[current_index_];
}

const Token& Parser::previous() const {
  return tokens_[current_index_ - 1];
}

const Token& Parser::expect(TokenType type) {
  if (check(type)) {
    return advance();
  }
  throw_unexpected_token(current());
}

void Parser::throw_unexpected_token(const Token& token) const {
  throw ParseError(unexpected_token_message(token), token.position);
}

}  // namespace calc