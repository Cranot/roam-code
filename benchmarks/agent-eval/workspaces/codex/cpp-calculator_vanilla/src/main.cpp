#include <cctype>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "calculator/calculator.h"
#include "calculator/format.h"
#include "calculator/repl.h"

namespace {

std::string trim_copy(const std::string& text) {
  std::size_t begin = 0;
  while (begin < text.size() &&
         std::isspace(static_cast<unsigned char>(text[begin])) != 0) {
    ++begin;
  }

  std::size_t end = text.size();
  while (end > begin &&
         std::isspace(static_cast<unsigned char>(text[end - 1])) != 0) {
    --end;
  }

  return text.substr(begin, end - begin);
}

void print_usage(const char* program_name) {
  std::cout << "Usage:\n"
            << "  " << program_name << "              # Interactive REPL\n"
            << "  " << program_name << " --file <path> # Evaluate expressions from file\n"
            << "  " << program_name << " --help        # Show help\n";
}

int evaluate_file(const std::string& path) {
  std::ifstream input(path);
  if (!input) {
    std::cerr << "Failed to open file: " << path << "\n";
    return 1;
  }

  calc::Calculator calculator;
  std::string line;
  std::size_t line_number = 0;
  bool had_error = false;

  while (std::getline(input, line)) {
    ++line_number;

    std::string trimmed = trim_copy(line);
    if (trimmed.empty() || trimmed.front() == '#') {
      continue;
    }

    try {
      const double result = calculator.evaluate(trimmed);
      std::cout << calc::format_number(result) << "\n";
    } catch (const std::exception& ex) {
      std::cerr << "Line " << line_number << ": " << ex.what() << "\n";
      had_error = true;
    }
  }

  return had_error ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 1) {
    calc::Calculator calculator;
    calc::Repl repl(calculator, std::cin, std::cout, std::cerr);
    repl.run();
    return 0;
  }

  const std::vector<std::string> args(argv + 1, argv + argc);

  if (args.size() == 1 && (args[0] == "--help" || args[0] == "-h")) {
    print_usage(argv[0]);
    return 0;
  }

  if (args.size() == 2 && (args[0] == "--file" || args[0] == "-f")) {
    return evaluate_file(args[1]);
  }

  std::cerr << "Invalid arguments. Use --help for usage.\n";
  return 1;
}