#pragma once

#include <iosfwd>
#include <string>

#include "calculator/engine.h"

namespace calculator {

class Repl {
public:
    int runInteractive(CalculatorEngine& engine, std::istream& input, std::ostream& output, std::ostream& error) const;
    int runFile(CalculatorEngine& engine, const std::string& file_path, std::ostream& output, std::ostream& error) const;

    static void printHelp(std::ostream& output);
};

}  // namespace calculator