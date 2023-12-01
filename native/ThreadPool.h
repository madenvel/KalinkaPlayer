#ifndef THREAD_POOL_H
#define THREAD_POOL_H

#include <condition_variable>
#include <functional>
#include <list>
#include <mutex>
#include <queue>
#include <thread>

class ThreadPool {
  std::queue<std::function<void(std::stop_token)>> queue;
  std::mutex m;
  std::condition_variable con;
  std::list<std::jthread> threads;
  bool terminate = false;

public:
  ThreadPool(size_t numWorkers = 1);
  ~ThreadPool();

  void enqueue(std::function<void(std::stop_token)> task);
  void stop();

private:
  void worker(std::stop_token token);
};

#endif