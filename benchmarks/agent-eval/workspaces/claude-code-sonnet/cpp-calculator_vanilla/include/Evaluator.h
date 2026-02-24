#ifndef EVALUATOR_H
#define EVALUATOR_H

#include <string>
#include <map>
#include <vector>
#include <stdexcept>
#include <functional>

class EvaluationError : public std::runtime_error {
public:
    explicit EvaluationError(const std::string& message);
};

class Evaluator {
private:
    std::map<std::string, double> variables;
    std::vector<double> history;
    std::map<std::string, std::function<double(const std::vector<double>&)>> functions;

    void initialize_constants();
    void initialize_functions();

public:
    Evaluator();

    void set_variable(const std::string& name, double value);
    double get_variable(const std::string& name) const;
    bool has_variable(const std::string& name) const;

    void add_to_history(double value);
    double get_from_history(size_t index) const;
    const std::vector<double>& get_history() const { return history; }

    double call_function(const std::string& name, const std::vector<double>& args) const;
    bool has_function(const std::string& name) const;
};

#endif // EVALUATOR_H
