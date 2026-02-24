#include "calculator/repl.h"

#include <cctype>
#include <fstream>
#include <string>

#include "calculator/errors.h"

namespace calculator {

namespace {

std::string trim(const std::string& value) {
    std::size_t start = 0;
    while (start < value.size() && std::isspace(static_cast<unsigned char>(value[start]))) {
        ++start;
    }

    std::size_t end = value.size();
    while (end > start && std::isspace(static_cast<unsigned char>(value[end - 1]))) {
        --end;
    }

    return value.substr(start, end - start);
}

}  // namespace

int Repl::runInteractive(CalculatorEngine& engine,
                         std::istream& input,
                         std::ostream& output,
                         std::ostream& error) const {
    output << "Calculator REPL. Type 'help' for usage, 'exit' to quit.\n";

    std::string line;
    while (true) {
        output << "calc> " << std::flush;
        if (!std::getline(input, line)) {
            output << "\n";
            break;
        }

        const std::string command = trim(line);
        if (command.empty()) {
            continue;
        }
        if (command == "exit" || command == "quit") {
            break;
        }
        if (command == "help") {
            printHelp(output);
            continue;
        }

        try {
            output << engine.evaluateExpression(command).toString() << '\n';
        } catch (const CalcError& ex) {
            error << ex.what() << '\n';
        }
    }

    return 0;
}

int Repl::runFile(CalculatorEngine& engine,
                  const std::string& file_path,
                  std::ostream& output,
                  std::ostream& error) const {
    std::ifstream file(file_path);
    if (!file) {
        error << "Unable to open file: " << file_path << '\n';
        return 1;
    }

    std::string line;
    std::size_t line_number = 0;
    while (std::getline(file, line)) {
        ++line_number;
        const std::string expression = trim(line);
        if (expression.empty() || expression[0] == '#') {
            continue;
        }

        try {
            output << engine.evaluateExpression(expression).toString() << '\n';
        } catch (const CalcError& ex) {
            error << "Line " << line_number << ": " << ex.what() << '\n';
            return 1;
        }
    }

    return 0;
}

void Repl::printHelp(std::ostream& output) {
    output << "Commands:\n";
    output << "  help        Show this message\n";
    output << "  exit, quit  Leave the REPL\n\n";
    output << "Features:\n";
    output << "  Operators: +, -, *, /, %, ^\n";
    output << "  Parentheses and unary minus\n";
    output << "  Functions: sin, cos, tan, sqrt, log, log10, abs, ceil, floor, min, max\n";
    output << "  Variables: x = 3.14\n";
    output << "  Constants: pi, e\n";
    output << "  History: $1, $2, ... (1-based prior results)\n";
}

}  // namespace calculator
