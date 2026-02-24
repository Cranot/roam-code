#pragma once

#include <stdexcept>

namespace calculator {

class CalcError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class LexerError : public CalcError {
public:
    using CalcError::CalcError;
};

class ParseError : public CalcError {
public:
    using CalcError::CalcError;
};

class EvalError : public CalcError {
public:
    using CalcError::CalcError;
};

}  // namespace calculator