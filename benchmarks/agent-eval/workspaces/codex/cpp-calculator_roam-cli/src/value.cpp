#include "calculator/value.h"

#include <cmath>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace calculator {

namespace {
constexpr double kIntegerTolerance = 1e-12;

std::string formatDouble(double value) {
    if (std::isnan(value)) {
        return "nan";
    }
    if (std::isinf(value)) {
        return value > 0 ? "inf" : "-inf";
    }

    std::ostringstream stream;
    stream << std::setprecision(15) << value;
    std::string result = stream.str();

    const std::size_t decimal_pos = result.find('.');
    if (decimal_pos == std::string::npos) {
        return result;
    }

    while (!result.empty() && result.back() == '0') {
        result.pop_back();
    }
    if (!result.empty() && result.back() == '.') {
        result.pop_back();
    }
    if (result.empty() || result == "-0") {
        return "0";
    }
    return result;
}

}  // namespace

Value::Value() : value_(0LL) {}

Value::Value(long long integer_value) : value_(integer_value) {}

Value::Value(double floating_value) : value_(floating_value) {}

Value Value::fromInteger(long long integer_value) {
    return Value(integer_value);
}

Value Value::fromDouble(double floating_value) {
    if (std::isfinite(floating_value)) {
        const double rounded = std::round(floating_value);
        const bool fits_integer = rounded >= static_cast<double>(std::numeric_limits<long long>::min()) &&
                                  rounded <= static_cast<double>(std::numeric_limits<long long>::max());
        if (fits_integer && std::fabs(rounded - floating_value) < kIntegerTolerance) {
            return Value(static_cast<long long>(rounded));
        }
    }
    return Value(floating_value);
}

bool Value::isInteger() const {
    return std::holds_alternative<long long>(value_);
}

long long Value::asInteger() const {
    if (std::holds_alternative<long long>(value_)) {
        return std::get<long long>(value_);
    }
    throw std::runtime_error("Value is not an integer");
}

double Value::asDouble() const {
    if (std::holds_alternative<long long>(value_)) {
        return static_cast<double>(std::get<long long>(value_));
    }
    return std::get<double>(value_);
}

std::string Value::toString() const {
    if (std::holds_alternative<long long>(value_)) {
        return std::to_string(std::get<long long>(value_));
    }
    return formatDouble(std::get<double>(value_));
}

}  // namespace calculator