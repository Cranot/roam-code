#ifndef CALCULATOR_EVALUATOR_H
#define CALCULATOR_EVALUATOR_H

#include "calculator/parser.h"
#include <string>
#include <unordered_map>
#include <vector>
#include <stdexcept>

namespace calculator {

class EvalError : public std::runtime_error {
public:
    EvalError(const std::string& msg) : std::runtime_error(msg) {}
};

class Evaluator {
public:
    Evaluator();

    // Evaluate an expression string, returning the result
    double evaluate(const std::string& input);

    // Evaluate an AST node
    double eval(const ASTNode& node);

    // Variable access
    void setVariable(const std::string& name, double value);
    double getVariable(const std::string& name) const;
    bool hasVariable(const std::string& name) const;
    const std::unordered_map<std::string, double>& variables() const;

    // History access
    const std::vector<double>& history() const;
    double getHistory(int index) const;
    void clearHistory();

private:
    std::unordered_map<std::string, double> variables_;
    std::vector<double> history_;

    double callFunction(const std::string& name, const std::vector<double>& args);
};

} // namespace calculator

#endif // CALCULATOR_EVALUATOR_H
