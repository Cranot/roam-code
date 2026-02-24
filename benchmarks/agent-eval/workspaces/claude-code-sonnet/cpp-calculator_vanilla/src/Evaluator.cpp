#include "Evaluator.h"
#include <cmath>
#include <algorithm>
#include <sstream>

EvaluationError::EvaluationError(const std::string& message)
    : std::runtime_error(message) {}

Evaluator::Evaluator() {
    initialize_constants();
    initialize_functions();
}

void Evaluator::initialize_constants() {
    variables["pi"] = M_PI;
    variables["e"] = M_E;
}

void Evaluator::initialize_functions() {
    // Trigonometric functions
    functions["sin"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("sin() expects 1 argument, got " + std::to_string(args.size()));
        }
        return std::sin(args[0]);
    };

    functions["cos"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("cos() expects 1 argument, got " + std::to_string(args.size()));
        }
        return std::cos(args[0]);
    };

    functions["tan"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("tan() expects 1 argument, got " + std::to_string(args.size()));
        }
        return std::tan(args[0]);
    };

    // Mathematical functions
    functions["sqrt"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("sqrt() expects 1 argument, got " + std::to_string(args.size()));
        }
        if (args[0] < 0) {
            throw EvaluationError("sqrt() of negative number");
        }
        return std::sqrt(args[0]);
    };

    functions["log"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("log() expects 1 argument, got " + std::to_string(args.size()));
        }
        if (args[0] <= 0) {
            throw EvaluationError("log() of non-positive number");
        }
        return std::log(args[0]);
    };

    functions["log10"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("log10() expects 1 argument, got " + std::to_string(args.size()));
        }
        if (args[0] <= 0) {
            throw EvaluationError("log10() of non-positive number");
        }
        return std::log10(args[0]);
    };

    functions["abs"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("abs() expects 1 argument, got " + std::to_string(args.size()));
        }
        return std::abs(args[0]);
    };

    functions["ceil"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("ceil() expects 1 argument, got " + std::to_string(args.size()));
        }
        return std::ceil(args[0]);
    };

    functions["floor"] = [](const std::vector<double>& args) {
        if (args.size() != 1) {
            throw EvaluationError("floor() expects 1 argument, got " + std::to_string(args.size()));
        }
        return std::floor(args[0]);
    };

    // Min/Max functions
    functions["min"] = [](const std::vector<double>& args) {
        if (args.size() < 1) {
            throw EvaluationError("min() expects at least 1 argument");
        }
        return *std::min_element(args.begin(), args.end());
    };

    functions["max"] = [](const std::vector<double>& args) {
        if (args.size() < 1) {
            throw EvaluationError("max() expects at least 1 argument");
        }
        return *std::max_element(args.begin(), args.end());
    };
}

void Evaluator::set_variable(const std::string& name, double value) {
    variables[name] = value;
}

double Evaluator::get_variable(const std::string& name) const {
    // Check for history reference ($1, $2, etc.)
    if (!name.empty() && name[0] == '$') {
        try {
            size_t index = std::stoul(name.substr(1));
            return get_from_history(index);
        } catch (const std::exception&) {
            throw EvaluationError("Invalid history reference: " + name);
        }
    }

    auto it = variables.find(name);
    if (it == variables.end()) {
        throw EvaluationError("Unknown variable: " + name);
    }
    return it->second;
}

bool Evaluator::has_variable(const std::string& name) const {
    return variables.find(name) != variables.end();
}

void Evaluator::add_to_history(double value) {
    history.push_back(value);
}

double Evaluator::get_from_history(size_t index) const {
    if (index < 1 || index > history.size()) {
        std::ostringstream oss;
        oss << "History index out of range: $" << index
            << " (valid range: $1 to $" << history.size() << ")";
        throw EvaluationError(oss.str());
    }
    return history[index - 1]; // 1-indexed for user
}

double Evaluator::call_function(const std::string& name, const std::vector<double>& args) const {
    auto it = functions.find(name);
    if (it == functions.end()) {
        throw EvaluationError("Unknown function: " + name);
    }
    return it->second(args);
}

bool Evaluator::has_function(const std::string& name) const {
    return functions.find(name) != functions.end();
}
