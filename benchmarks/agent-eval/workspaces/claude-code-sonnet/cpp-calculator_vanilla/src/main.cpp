#include "Lexer.h"
#include "Parser.h"
#include "Evaluator.h"
#include <iostream>
#include <fstream>
#include <string>
#include <iomanip>

void print_help() {
    std::cout << "Calculator REPL - Help\n";
    std::cout << "======================\n\n";
    std::cout << "Operators:\n";
    std::cout << "  +, -, *, /, % (modulo), ^ (power)\n\n";
    std::cout << "Functions:\n";
    std::cout << "  sin, cos, tan, sqrt, log, log10, abs, ceil, floor, min, max\n\n";
    std::cout << "Constants:\n";
    std::cout << "  pi, e\n\n";
    std::cout << "Variables:\n";
    std::cout << "  x = 3.14    (assign value to variable)\n";
    std::cout << "  x * 2       (use variable in expression)\n\n";
    std::cout << "History:\n";
    std::cout << "  $1, $2, ... (recall previous results)\n\n";
    std::cout << "Commands:\n";
    std::cout << "  help        (show this help)\n";
    std::cout << "  quit/exit   (exit calculator)\n\n";
    std::cout << "Examples:\n";
    std::cout << "  2 + 3 * 4\n";
    std::cout << "  sin(pi / 4)\n";
    std::cout << "  x = 5\n";
    std::cout << "  sqrt(x^2 + 3^2)\n";
    std::cout << "  min(1, 2, 3) + max(4, 5, 6)\n";
    std::cout << "  $1 * 2      (use first result)\n";
}

std::string evaluate_expression(const std::string& input, Evaluator& evaluator, bool show_errors = true) {
    try {
        Lexer lexer(input);
        std::vector<Token> tokens = lexer.tokenize();

        // Check for invalid tokens
        for (const auto& token : tokens) {
            if (token.type == TokenType::INVALID) {
                return "Error: Unexpected token '" + token.value + "' at position " + std::to_string(token.position);
            }
        }

        Parser parser(tokens);
        auto ast = parser.parse();
        double result = ast->evaluate(evaluator);

        evaluator.add_to_history(result);

        std::ostringstream oss;
        oss << std::setprecision(15) << result;
        return oss.str();

    } catch (const ParseError& e) {
        if (show_errors) {
            return std::string("Parse error: ") + e.what();
        }
        throw;
    } catch (const EvaluationError& e) {
        if (show_errors) {
            return std::string("Evaluation error: ") + e.what();
        }
        throw;
    } catch (const std::exception& e) {
        if (show_errors) {
            return std::string("Error: ") + e.what();
        }
        throw;
    }
}

void repl_mode() {
    Evaluator evaluator;
    std::string input;

    std::cout << "Calculator REPL (type 'help' for help, 'quit' to exit)\n";

    while (true) {
        std::cout << "calc> ";
        std::getline(std::cin, input);

        // Trim whitespace
        size_t start = input.find_first_not_of(" \t\n\r");
        size_t end = input.find_last_not_of(" \t\n\r");
        if (start == std::string::npos) {
            continue; // Empty line
        }
        input = input.substr(start, end - start + 1);

        if (input == "quit" || input == "exit") {
            break;
        }

        if (input == "help") {
            print_help();
            continue;
        }

        if (input.empty()) {
            continue;
        }

        std::string result = evaluate_expression(input, evaluator);
        std::cout << result << "\n";
    }
}

void file_mode(const std::string& filename) {
    std::ifstream file(filename);
    if (!file.is_open()) {
        std::cerr << "Error: Cannot open file '" << filename << "'\n";
        return;
    }

    Evaluator evaluator;
    std::string line;
    int line_number = 0;

    while (std::getline(file, line)) {
        line_number++;

        // Trim whitespace
        size_t start = line.find_first_not_of(" \t\n\r");
        size_t end = line.find_last_not_of(" \t\n\r");

        if (start == std::string::npos || line.empty()) {
            continue; // Empty line
        }

        line = line.substr(start, end - start + 1);

        // Skip comments
        if (line[0] == '#') {
            continue;
        }

        std::cout << "Line " << line_number << ": " << line << "\n";
        std::string result = evaluate_expression(line, evaluator);
        std::cout << "  Result: " << result << "\n";
    }

    file.close();
}

int main(int argc, char* argv[]) {
    if (argc == 1) {
        // No arguments - REPL mode
        repl_mode();
    } else if (argc == 2) {
        // One argument - file evaluation mode
        file_mode(argv[1]);
    } else {
        std::cerr << "Usage:\n";
        std::cerr << "  " << argv[0] << "           (interactive REPL mode)\n";
        std::cerr << "  " << argv[0] << " <file>    (evaluate expressions from file)\n";
        return 1;
    }

    return 0;
}
