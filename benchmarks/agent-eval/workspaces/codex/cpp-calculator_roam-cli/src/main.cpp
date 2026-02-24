#include <iostream>
#include <string>

#include "calculator/repl.h"

int main(int argc, char* argv[]) {
    calculator::CalculatorEngine engine;
    calculator::Repl repl;

    if (argc == 1) {
        return repl.runInteractive(engine, std::cin, std::cout, std::cerr);
    }

    const std::string first_arg = argv[1];
    if (first_arg == "-h" || first_arg == "--help") {
        std::cout << "Usage:\n";
        std::cout << "  calc                 Start interactive REPL\n";
        std::cout << "  calc --file <path>   Evaluate expressions from file\n";
        std::cout << "  calc <path>          Shortcut for file mode\n\n";
        calculator::Repl::printHelp(std::cout);
        return 0;
    }

    if (first_arg == "-f" || first_arg == "--file") {
        if (argc < 3) {
            std::cerr << "Missing file path for --file option\n";
            return 1;
        }
        return repl.runFile(engine, argv[2], std::cout, std::cerr);
    }

    if (argc == 2) {
        return repl.runFile(engine, first_arg, std::cout, std::cerr);
    }

    std::cerr << "Unknown arguments. Use --help for usage.\n";
    return 1;
}