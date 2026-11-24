#pragma once

#include <array>
#include <cstddef>
#include <string>
#include <utility>
#include <vector>

// Required by the OpenM++ common module.
using std_string = std::string;

enum class Risk : std::size_t {
    JobExit,
    Relocation,
    HealthTransition,
    SharedCare,
    Partnership,
    Death,
    Count,
};

struct RiskSchedule {
    double rate = 0.0;
    double start = 0.0;
    double forced = -1.0;
    double draw = 0.5;
    bool specified = false;
};

struct case_info {
    double cutoff = 0.0;
    bool alive_at_cutoff = true;
    std::array<RiskSchedule, static_cast<std::size_t>(Risk::Count)> risks{};
    std::vector<std::pair<Risk, double>> events;

    double scheduled_day(Risk risk, double eligible_from = 0.0) const;
};
