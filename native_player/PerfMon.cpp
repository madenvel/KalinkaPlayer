#ifdef PROFILE
#include "PerfMon.h"

#include <iostream>
#include <numeric>

PerfMon::PerfMon() {}

void PerfMon::begin(const std::string &mark) {
  auto &perfMark = marks[mark];
  if (perfMark.open) {
    return;
  }

  perfMark.open = true;
  perfMark.point = std::chrono::high_resolution_clock::now();
}

void PerfMon::end(const std::string &mark) {
  auto now = std::chrono::high_resolution_clock::now();
  auto &perfMark = marks[mark];
  if (!perfMark.open) {
    return;
  }

  auto duration =
      std::chrono::duration_cast<std::chrono::nanoseconds>(now - perfMark.point)
          .count();

  if (perfMark.durations.full()) {
    auto size = perfMark.durations.capacity();
    perfMark.averageNs +=
        (double)(duration - perfMark.durations.front()) / size;
    perfMark.durations.pop_front();
  } else {
    perfMark.averageNs =
        (perfMark.averageNs * perfMark.durations.size() + duration) /
        (perfMark.durations.size() + 1);
  }

  perfMark.durations.push_back(duration);
  perfMark.open = false;
}

void PerfMon::printStats() {
  for (const auto &mark : marks) {
    std::cout << "PerfMon: " << mark.first << " average "
              << mark.second.averageNs / 1000.0 << "us" << std::endl;
  }
}

void PerfMon::printPeriodically(int seconds) {
  if (printThread.joinable()) {
    printThread.request_stop();
    printThread.join();
  }

  printThread = std::jthread([this, seconds](std::stop_token token) {
    while (!token.stop_requested()) {
      std::this_thread::sleep_for(std::chrono::seconds(seconds));
      printStats();
    }
  });
}

PerfMon::~PerfMon() {
  if (printThread.joinable()) {
    printThread.request_stop();
    printThread.join();
  }

  printStats();
}

#endif // PROFILE