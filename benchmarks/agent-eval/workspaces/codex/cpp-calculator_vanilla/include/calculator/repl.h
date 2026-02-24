#pragma once

#include <istream>
#include <ostream>

#include "calculator/calculator.h"

namespace calc {

class Repl {
 public:
  Repl(Calculator& calculator, std::istream& input, std::ostream& output,
       std::ostream& error);

  void run();
  static void print_help(std::ostream& output);

 private:
  Calculator& calculator_;
  std::istream& input_;
  std::ostream& output_;
  std::ostream& error_;
};

}  // namespace calc