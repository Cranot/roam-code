#include "calculator/repl.h"
#include <iostream>
#include <string>
#include <cstring>
#include <cmath>

void printUsage(const char* program) {
    std::cout << "Usage: " << program << " [options] [file]\n"
              << "\n"
              << "Options:\n"
              << "  -h, --help     Show this help message\n"
              << "  -e EXPR        Evaluate a single expression and exit\n"
              << "  FILE           Evaluate expressions from a file\n"
              << "\n"
              << "With no arguments, starts interactive mode.\n";
}

int main(int argc, char* argv[]) {
    calculator::REPL repl;

    if (argc == 1) {
        // Interactive mode
        repl.run();
        return 0;
    }

    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];

        if (arg == "-h" || arg == "--help") {
            printUsage(argv[0]);
            return 0;
        }

        if (arg == "-e") {
            if (i + 1 >= argc) {
                std::cerr << "Error: -e requires an expression argument\n";
                return 1;
            }
            i++;
            calculator::Evaluator evaluator;
            try {
                double result = evaluator.evaluate(argv[i]);
                // Format: integer if exact, otherwise floating point
                if (result == static_cast<long long>(result) &&
                    std::abs(result) < 1e15) {
                    std::cout << static_cast<long long>(result) << "\n";
                } else {
                    std::cout << result << "\n";
                }
            } catch (const std::exception& e) {
                std::cerr << "Error: " << e.what() << "\n";
                return 1;
            }
            return 0;
        }

        // Treat as filename
        repl.evaluateFile(arg);
    }

    return 0;
}
