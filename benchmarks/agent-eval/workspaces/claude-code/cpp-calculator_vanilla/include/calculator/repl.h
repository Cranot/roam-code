#ifndef CALCULATOR_REPL_H
#define CALCULATOR_REPL_H

#include "calculator/evaluator.h"
#include <string>
#include <iostream>

namespace calculator {

class REPL {
public:
    REPL();

    // Run interactive REPL loop
    void run(std::istream& in = std::cin, std::ostream& out = std::cout);

    // Evaluate a file of expressions, print results
    void evaluateFile(const std::string& filename, std::ostream& out = std::cout);

private:
    Evaluator evaluator_;

    void printHelp(std::ostream& out) const;
    void processLine(const std::string& line, std::ostream& out);
    std::string formatResult(double value) const;
};

} // namespace calculator

#endif // CALCULATOR_REPL_H
