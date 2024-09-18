#ifndef PERF_MON_H
#define PERF_MON_H

#ifdef PROFILE
#define perfmon_begin(mark) PerfMon::getInstance().begin(mark)
#define perfmon_end(mark) PerfMon::getInstance().end(mark)
#define perfmon_print_stats() PerfMon::getInstance().printStats()
#define perfmon_print_periodically(seconds)                                    \
  PerfMon::getInstance().printPeriodically(seconds)
#else
#define perfmon_begin(mark)
#define perfmon_end(mark)
#define perfmon_print_stats()
#define perfmon_print_periodically(seconds)
#endif

#ifdef PROFILE

#include <boost/circular_buffer.hpp>
#include <chrono>
#include <iostream>
#include <thread>
#include <unordered_map>

class PerfMon {
public:
  static PerfMon &getInstance() {
    static PerfMon instance;
    return instance;
  }

  void begin(const std::string &mark);
  void end(const std::string &mark);

  void printStats();
  void printPeriodically(int seconds);

  double getAverageNs(const std::string &mark) { return marks[mark].averageNs; }

private:
  struct PerfMark {
    std::chrono::high_resolution_clock::time_point point;
    bool open;
    double averageNs;
    boost::circular_buffer<long> durations;

    PerfMark() : open(false), averageNs(0.0), durations(10) {}
  };
  std::unordered_map<std::string, PerfMark> marks;

  std::jthread printThread;

  PerfMon();
  ~PerfMon();
  PerfMon(const PerfMon &) = delete;
  PerfMon &operator=(const PerfMon &) = delete;
};

#endif // PROFILE
#endif // PERF_MON_H