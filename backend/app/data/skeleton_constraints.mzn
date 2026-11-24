int: task_count;
set of int: Tasks = 1..task_count;
int: conflict_count;
set of int: Conflicts = 1..conflict_count;
int: day_minutes;
int: cutoff_day;

array[Tasks] of int: task_day;
array[Tasks] of int: earliest_day;
array[Tasks] of bool: valid_input;
array[Tasks] of int: fixed_start; % -1 denotes flexible within the recorded day
array[Tasks] of int: duration_minutes;
array[Tasks] of int: cost_cents;
array[Tasks] of int: budget_cents; % -1 denotes absent
array[Conflicts] of Tasks: conflict_first;
array[Conflicts] of Tasks: conflict_second;

array[Tasks] of var 0..day_minutes: start_minute;

constraint forall (task in Tasks) (
    valid_input[task] /\
    earliest_day[task] <= task_day[task] /\
    task_day[task] <= cutoff_day /\
    start_minute[task] + duration_minutes[task] <= day_minutes /\
    (fixed_start[task] >= 0 -> start_minute[task] = fixed_start[task])
);

constraint forall (pair in Conflicts) (
    let {
        int: first = conflict_first[pair],
        int: second = conflict_second[pair]
    } in
    start_minute[first] + duration_minutes[first] <= start_minute[second] \/
    start_minute[second] + duration_minutes[second] <= start_minute[first]
);

constraint forall (task in Tasks where budget_cents[task] >= 0) (
    sum (other in Tasks where task_day[other] = task_day[task]) (cost_cents[other])
    <= budget_cents[task]
);

solve satisfy;
output ["feasible"];
