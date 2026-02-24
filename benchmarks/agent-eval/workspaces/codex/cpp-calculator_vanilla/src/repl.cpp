#include "calculator/repl.h"

#include <cctype>
#include <exception>
#include <string>

#include "calculator/format.h"

namespace calc {
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

}  // namespace

Repl::Repl(Calculator& calculator, std::istream& input, std::ostream& output,
           std::ostream& error)
    : calculator_(calculator), input_(input), output_(output), error_(error) {}

void Repl::run() {
  output_ << "Type 'help' for commands.\n";

  std::string line;
  while (true) {
    output_ << "calc> " << std::flush;

    if (!std::getline(input_, line)) {
      output_ << "\n";
      break;
    }

    std::string trimmed = trim_copy(line);
    if (trimmed.empty()) {
      continue;
    }

    if (trimmed == "quit" || trimmed == "exit") {
      break;
    }

    if (trimmed == "help") {
      print_help(output_);
      continue;
    }

    if (trimmed == "history") {
      const auto& history = calculator_.context().history();
      if (history.empty()) {
        output_ << "(history is empty)\n";
      } else {
        for (std::size_t i = 0; i < history.size(); ++i) {
          output_ << "$" << (i + 1) << " = " << format_number(history[i])
                  << "\n";
        }
      }
      continue;
    }

    try {
      const double result = calculator_.evaluate(trimmed);
      output_ << "= " << format_number(result) << "\n";
    } catch (const std::exception& ex) {
      error_ << "Error: " << ex.what() << "\n";
    }
  }
}

void Repl::print_help(std::ostream& output) {
  output << "Commands:\n"
         << "  help     Show this help text\n"
         << "  history  Show computed results ($1, $2, ...)\n"
         << "  quit     Exit the calculator\n"
         << "  exit     Exit the calculator\n"
         << "\n"
         << "Examples:\n"
         << "  2 + 3 * 4\n"
         << "  x = 3.14\n"
         << "  sin(pi / 2)\n"
         << "  max($1, 10)\n";
}

}  // namespace calc