#include "calculator/repl.h"
#include <fstream>
#include <sstream>
#include <cmath>
#include <iomanip>

namespace calculator {

REPL::REPL() {}

void REPL::run(std::istream& in, std::ostream& out) {
    out << "Calculator v1.0 - Type 'help' for usage, 'quit' to exit\n";

    std::string line;
    while (true) {
        out << "> ";
        out.flush();
        if (!std::getline(in, line)) break;

        // Trim whitespace
        size_t start = line.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) continue;
        size_t end = line.find_last_not_of(" \t\r\n");
        line = line.substr(start, end - start + 1);

        if (line.empty()) continue;
        if (line == "quit" || line == "exit") break;
        if (line == "help") {
            printHelp(out);
            continue;
        }
        if (line == "vars") {
            for (const auto& [name, value] : evaluator_.variables()) {
                out << "  " << name << " = " << formatResult(value) << "\n";
            }
            continue;
        }
        if (line == "history") {
            const auto& hist = evaluator_.history();
            for (size_t i = 0; i < hist.size(); i++) {
                out << "  $" << (i + 1) << " = " << formatResult(hist[i]) << "\n";
            }
            continue;
        }
        if (line == "clear") {
            evaluator_.clearHistory();
            out << "History cleared.\n";
            continue;
        }

        processLine(line, out);
    }
}

void REPL::evaluateFile(const std::string& filename, std::ostream& out) {
    std::ifstream file(filename);
    if (!file.is_open()) {
        out << "Error: Cannot open file '" << filename << "'\n";
        return;
    }

    std::string line;
    int lineNum = 0;
    while (std::getline(file, line)) {
        lineNum++;

        // Trim whitespace
        size_t start = line.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) continue;
        size_t end = line.find_last_not_of(" \t\r\n");
        line = line.substr(start, end - start + 1);

        if (line.empty()) continue;
        if (line[0] == '#') continue; // comments

        out << line << " = ";
        processLine(line, out);
    }
}

void REPL::printHelp(std::ostream& out) const {
    out << "\n"
        << "Usage:\n"
        << "  Arithmetic:   2 + 3, 10 / 3, 7 % 3, 2 ^ 10\n"
        << "  Parentheses:  (1 + 2) * 3\n"
        << "  Unary minus:  -5, -(3 + 2)\n"
        << "  Functions:    sin(pi/2), sqrt(16), log(e), max(3, 5)\n"
        << "  Variables:    x = 42, y = x * 2\n"
        << "  Constants:    pi, e\n"
        << "  History:      $1, $2 (reference previous results)\n"
        << "\n"
        << "Functions: sin, cos, tan, sqrt, log, log10, abs, ceil, floor, min, max\n"
        << "\n"
        << "Commands:\n"
        << "  help     Show this help message\n"
        << "  vars     List all variables\n"
        << "  history  Show expression history\n"
        << "  clear    Clear history\n"
        << "  quit     Exit the calculator\n"
        << "\n";
}

void REPL::processLine(const std::string& line, std::ostream& out) {
    try {
        double result = evaluator_.evaluate(line);
        out << formatResult(result) << "\n";
    } catch (const LexerError& e) {
        out << "Error: " << e.what() << "\n";
    } catch (const ParseError& e) {
        out << "Error: " << e.what() << "\n";
    } catch (const EvalError& e) {
        out << "Error: " << e.what() << "\n";
    }
}

std::string REPL::formatResult(double value) const {
    // If it's an integer value, display without decimal point
    if (std::isfinite(value) && value == std::floor(value) &&
        std::abs(value) < 1e15) {
        std::ostringstream oss;
        oss << std::fixed << std::setprecision(0) << value;
        return oss.str();
    }

    std::ostringstream oss;
    oss << std::setprecision(10) << value;
    std::string s = oss.str();

    // Remove trailing zeros after decimal point
    if (s.find('.') != std::string::npos) {
        size_t last = s.find_last_not_of('0');
        if (last != std::string::npos && s[last] == '.') last--;
        s = s.substr(0, last + 1);
    }

    return s;
}

} // namespace calculator
