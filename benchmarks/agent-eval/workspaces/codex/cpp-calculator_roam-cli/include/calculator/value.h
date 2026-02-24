#pragma once

#include <cstddef>
#include <string>
#include <variant>

namespace calculator {

class Value {
public:
    Value();
    explicit Value(long long integer_value);
    explicit Value(double floating_value);

    static Value fromInteger(long long integer_value);
    static Value fromDouble(double floating_value);

    bool isInteger() const;
    long long asInteger() const;
    double asDouble() const;

    std::string toString() const;

private:
    std::variant<long long, double> value_;
};

}  // namespace calculator