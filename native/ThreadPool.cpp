#include "ThreadPool.h"
#include <iostream>

ThreadPool::ThreadPool(size_t numWorkers) {
  for (size_t i = 0; i < numWorkers; ++i) {
    threads.push_back(
        std::move(std::jthread(std::bind_front(&ThreadPool::worker, this))));
  }
}

ThreadPool::~ThreadPool() { stop(); }

void ThreadPool::enqueue(std::function<void(std::stop_token)> task) {
  if (terminate) {
    return;
  }
  {
    std::lock_guard<std::mutex> lock(m);
    queue.push(task);
  }
  con.notify_one();
}

void ThreadPool::stop() {
  terminate = true;
  con.notify_all();
  for (auto &thread : threads) {
    if (thread.joinable()) {
      thread.join();
    }
  }
}

void ThreadPool::worker(std::stop_token token) {
  std::unique_lock<std::mutex> lock(m);
  while (!terminate) {
    con.wait(lock, [this]() { return !queue.empty() || terminate; });
    if (terminate) {
      break;
    }
    auto task = queue.front();
    queue.pop();
    lock.unlock();
    try {
      task(token);
    } catch (std::exception &e) {
      std::cerr << "Exception caught in a thread: " << e.what() << std::endl;
    }
    lock.lock();
  }
}
