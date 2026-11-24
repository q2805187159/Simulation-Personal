#include "custom_early.h"
#include "custom.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {

constexpr std::array<std::string_view, static_cast<std::size_t>(Risk::Count)> risk_codes{
    "job-exit", "relocation", "health-transition", "shared-care", "partnership", "death",
};

std::size_t risk_index(Risk risk)
{
    return static_cast<std::size_t>(risk);
}

std::vector<std::string> split_tsv(std::string line)
{
    if (!line.empty() && line.back() == '\r') {
        line.pop_back();
    }
    std::vector<std::string> fields;
    std::size_t start = 0;
    for (auto end = line.find('\t'); end != std::string::npos; end = line.find('\t', start)) {
        fields.push_back(line.substr(start, end - start));
        start = end + 1;
    }
    fields.push_back(line.substr(start));
    return fields;
}

double read_number(const std::string& text)
{
    std::istringstream field(text);
    field.imbue(std::locale::classic());
    double value = 0.0;
    if (!(field >> std::noskipws >> value) || field.peek() != EOF || !std::isfinite(value)) {
        throw std::runtime_error("Invalid number in case.tsv");
    }
    return value;
}

} // namespace

double case_info::scheduled_day(Risk risk, double eligible_from) const
{
    constexpr double unscheduled = std::numeric_limits<double>::infinity();
    const auto& schedule = risks[risk_index(risk)];
    const double start = std::max(schedule.start, eligible_from);
    if (start > cutoff || (risk == Risk::Death && alive_at_cutoff)) {
        return unscheduled;
    }
    if (schedule.forced >= 0.0) {
        return schedule.forced >= start && schedule.forced <= cutoff ? schedule.forced : unscheduled;
    }
    if (schedule.rate == 0.0) {
        return unscheduled;
    }
    const double day = start - std::log1p(-schedule.draw) / schedule.rate;
    return day <= cutoff ? day : unscheduled;
}

void ReadCase(case_info& ci)
{
    std::ifstream input("case.tsv");
    std::string line;
    std::getline(input, line);
    const auto config = split_tsv(line);
    if (config.size() != 3 || config[0] != "SKELETON_CASE_V1" || (config[2] != "0" && config[2] != "1")) {
        throw std::runtime_error("Invalid case header in case.tsv");
    }
    ci.cutoff = read_number(config[1]);
    ci.alive_at_cutoff = config[2] == "1";
    std::getline(input, line);
    if (ci.cutoff < 0.0 || split_tsv(line) != std::vector<std::string>{"code", "rate_per_day", "start_day", "forced_day", "draw"}) {
        throw std::runtime_error("Invalid cutoff or risk header in case.tsv");
    }
    while (std::getline(input, line)) {
        const auto fields = split_tsv(line);
        if (fields.size() != 5) {
            throw std::runtime_error("Invalid risk row in case.tsv");
        }
        const auto found = std::find(risk_codes.begin(), risk_codes.end(), fields[0]);
        if (found == risk_codes.end()) {
            throw std::runtime_error("Unknown risk in case.tsv");
        }
        auto& schedule = ci.risks[found - risk_codes.begin()];
        if (schedule.specified) {
            throw std::runtime_error("Repeated risk in case.tsv");
        }
        schedule = {read_number(fields[1]), read_number(fields[2]), read_number(fields[3]), read_number(fields[4]), true};
        if (schedule.rate < 0.0 || schedule.start < 0.0 ||
            (schedule.forced != -1.0 && schedule.forced < schedule.start) ||
            schedule.draw <= 0.0 || schedule.draw >= 1.0) {
            throw std::runtime_error("Risk outside its numeric bounds in case.tsv");
        }
    }
    if (!input.eof()) {
        throw std::runtime_error("Unable to read case.tsv");
    }
    const auto& death = ci.risks[risk_index(Risk::Death)];
    if (!ci.alive_at_cutoff && (death.forced < 0.0 || death.forced > ci.cutoff)) {
        throw std::runtime_error("A deceased case requires death by the cutoff in case.tsv");
    }
}

void WriteEvents(const case_info& ci)
{
    std::ofstream output("events.tsv");
    output.exceptions(std::ios::failbit | std::ios::badbit);
    output.imbue(std::locale::classic());
    output << "code\telapsed_day\n" << std::setprecision(std::numeric_limits<double>::max_digits10);
    for (const auto& [risk, day] : ci.events) {
        output << risk_codes[risk_index(risk)] << '\t' << day << '\n';
    }
    output.close();
}
