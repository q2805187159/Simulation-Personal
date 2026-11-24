#pragma once

case_info* CaseInfo(case_info* ci = nullptr, bool reset = false);
void SimulateEvents();
void ReadCase(case_info& ci);
void WriteEvents(const case_info& ci);
