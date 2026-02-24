#include "calculator/evaluator.h"
#include <cmath>
#include <sstream>
#include <algorithm>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#ifndef M_E
#define M_E 2.71828182845904523536
#endif

namespace calculator {

Evaluator::Evaluator() {
    // Built-in constants
    variables_["pi"] = M_PI;
    variables_["e"] = M_E;
}

double Evaluator::evaluate(const std::string& input) {
    Lexer lexer(input);
    auto tokens = lexer.tokenize();
    Parser parser(tokens);
    auto ast = parser.parse();
    double result = eval(*ast);

    // Store in history (assignments too)
    history_.push_back(result);

    return result;
}

double Evaluator::eval(const ASTNode& node) {
    switch (node.type) {
        case NodeType::Number:
            return node.numValue;

        case NodeType::UnaryMinus:
            return -eval(*node.children[0]);

        case NodeType::BinaryOp: {
            double left = eval(*node.children[0]);
            double right = eval(*node.children[1]);

            if (node.op == "+") return left + right;
            if (node.op == "-") return left - right;
            if (node.op == "*") return left * right;
            if (node.op == "/") {
                if (right == 0.0) throw EvalError("Division by zero");
                return left / right;
            }
            if (node.op == "%") {
                if (right == 0.0) throw EvalError("Modulo by zero");
                return std::fmod(left, right);
            }
            if (node.op == "^") return std::pow(left, right);

            throw EvalError("Unknown operator '" + node.op + "'");
        }

        case NodeType::Variable: {
            const std::string& name = node.strValue;
            auto it = variables_.find(name);
            if (it == variables_.end()) {
                throw EvalError("Unknown variable '" + name + "'");
            }
            return it->second;
        }

        case NodeType::Assignment: {
            const std::string& name = node.strValue;
            if (name == "pi" || name == "e") {
                throw EvalError("Cannot reassign built-in constant '" + name + "'");
            }
            double value = eval(*node.children[0]);
            variables_[name] = value;
            return value;
        }

        case NodeType::FunctionCall: {
            const std::string& name = node.strValue;
            std::vector<double> args;
            for (const auto& child : node.children) {
                args.push_back(eval(*child));
            }
            return callFunction(name, args);
        }

        case NodeType::HistoryRef: {
            int index = static_cast<int>(node.numValue);
            return getHistory(index);
        }
    }

    throw EvalError("Unknown node type");
}

double Evaluator::callFunction(const std::string& name, const std::vector<double>& args) {
    // Single-argument functions
    if (name == "sin" || name == "cos" || name == "tan" ||
        name == "sqrt" || name == "log" || name == "log10" ||
        name == "abs" || name == "ceil" || name == "floor") {
        if (args.size() != 1) {
            std::ostringstream oss;
            oss << "Function '" << name << "' expects 1 argument, got " << args.size();
            throw EvalError(oss.str());
        }
        double x = args[0];

        if (name == "sin")   return std::sin(x);
        if (name == "cos")   return std::cos(x);
        if (name == "tan")   return std::tan(x);
        if (name == "sqrt") {
            if (x < 0) throw EvalError("sqrt of negative number");
            return std::sqrt(x);
        }
        if (name == "log") {
            if (x <= 0) throw EvalError("log of non-positive number");
            return std::log(x);
        }
        if (name == "log10") {
            if (x <= 0) throw EvalError("log10 of non-positive number");
            return std::log10(x);
        }
        if (name == "abs")   return std::abs(x);
        if (name == "ceil")  return std::ceil(x);
        if (name == "floor") return std::floor(x);
    }

    // Two-argument functions
    if (name == "min" || name == "max") {
        if (args.size() != 2) {
            std::ostringstream oss;
            oss << "Function '" << name << "' expects 2 arguments, got " << args.size();
            throw EvalError(oss.str());
        }
        if (name == "min") return std::min(args[0], args[1]);
        if (name == "max") return std::max(args[0], args[1]);
    }

    throw EvalError("Unknown function '" + name + "'");
}

void Evaluator::setVariable(const std::string& name, double value) {
    variables_[name] = value;
}

double Evaluator::getVariable(const std::string& name) const {
    auto it = variables_.find(name);
    if (it == variables_.end()) {
        throw EvalError("Unknown variable '" + name + "'");
    }
    return it->second;
}

bool Evaluator::hasVariable(const std::string& name) const {
    return variables_.find(name) != variables_.end();
}

const std::unordered_map<std::string, double>& Evaluator::variables() const {
    return variables_;
}

const std::vector<double>& Evaluator::history() const {
    return history_;
}

double Evaluator::getHistory(int index) const {
    if (index < 1 || index > static_cast<int>(history_.size())) {
        std::ostringstream oss;
        oss << "History reference $" << index << " is out of range (1.."
            << history_.size() << ")";
        throw EvalError(oss.str());
    }
    return history_[index - 1]; // 1-indexed
}

void Evaluator::clearHistory() {
    history_.clear();
}

} // namespace calculator
