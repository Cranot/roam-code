#include "calculator/format.h"

#include <cmath>
#include <iomanip>
#include <sstream>

namespace calc {

std::string format_number(double value) {
  if (std::isnan(value)) {
    return "nan";
  }
  if (std::isinf(value)) {
    return value > 0 ? "inf" : "-inf";
  }

  std::ostringstream stream;
  stream << std::setprecision(15) << value;
  std::string text = stream.str();

  const std::size_t dot = text.find('.');
  const std::size_t exponent = text.find_first_of("eE");

  if (dot != std::string::npos && exponent == std::string::npos) {
    while (!text.empty() && text.back() == '0') {
      text.pop_back();
    }
    if (!text.empty() && text.back() == '.') {
      text.pop_back();
    }
  }

  if (text == "-0") {
    return "0";
  }

  return text;
}

}  // namespace calc