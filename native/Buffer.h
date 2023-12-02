#ifndef BUFFER_H
#define BUFFER_H

#include <atomic>
#include <cassert>
#include <condition_variable>
#include <deque>
#include <functional>
#include <iostream>
#include <mutex>
#include <stop_token>

template <class T> class DequeBuffer {
  std::deque<T> data;
  std::mutex m;
  std::condition_variable_any hasDataCon;
  std::condition_variable_any hasSpaceCon;
  std::atomic<bool> eof;
  size_t maxSize;

public:
  DequeBuffer(size_t maxSize = 0) : eof(false), maxSize(maxSize) {}
  DequeBuffer(const DequeBuffer &) = delete;

  size_t max_size() const { return maxSize; }

  size_t read(T *dest, size_t size) {
    size_t sizeToCopy = 0;
    {
      std::lock_guard<std::mutex> lock(m);
      auto availableData = data.size();
      if (availableData > 0) {
        sizeToCopy = std::min(availableData, size);
        std::copy_n(data.begin(), sizeToCopy, dest);
        data.erase(data.begin(), data.begin() + sizeToCopy);
      }
    }

    if (sizeToCopy != 0) {
      hasSpaceCon.notify_one();
    }

    return sizeToCopy;
  }

  size_t write(const T *source, size_t size) {
    size_t sizeToCopy = 0;
    {
      std::lock_guard<std::mutex> lock(m);
      sizeToCopy = maxSize == 0 ? size : std::min(size, maxSize - data.size());
      data.insert(data.end(), source, source + sizeToCopy);
      assert(data.size() <= maxSize);
    }

    if (sizeToCopy != 0) {
      hasDataCon.notify_one();
    }
    return sizeToCopy;
  }

  void waitForData(std::stop_token stopToken = std::stop_token(),
                   size_t size = 1) {
    std::unique_lock<std::mutex> lock(m);
    hasDataCon.wait(lock, stopToken, [this, &size] {
      return data.size() >= size || eof.load();
    });
  }

  void waitForSpace(std::stop_token stopToken = std::stop_token(),
                    size_t size = 1) {
    if (maxSize != 0) {
      std::unique_lock<std::mutex> lock(m);
      hasSpaceCon.wait(lock, stopToken, [this, &size]() {
        return (maxSize - data.size()) >= size;
      });
    }
  }

  size_t size() const { return data.size(); }

  bool empty() const { return data.empty(); }

  void setEof() {
    eof.store(true);
    hasDataCon.notify_all();
  }

  bool isEof() { return eof.load(); }
};

#endif