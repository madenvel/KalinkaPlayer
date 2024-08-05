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
  mutable std::mutex m;
  std::condition_variable_any hasDataCon;
  std::condition_variable_any hasSpaceCon;
  std::atomic<bool> eof;
  size_t maxSize;
  std::function<void(DequeBuffer<T> &)> onEmptyCallback;
  bool done = false;

public:
  DequeBuffer(
      size_t maxSize = 0,
      std::function<void(DequeBuffer<T> &)> onEmptyCallback =
          [](DequeBuffer<T> &) {})
      : eof(false), maxSize(maxSize), onEmptyCallback(onEmptyCallback) {}

  ~DequeBuffer() {
    {
      std::lock_guard<std::mutex> lock(m);
      done = true;
    }
    hasDataCon.notify_all();
    hasSpaceCon.notify_all();
  }

  DequeBuffer(const DequeBuffer &) = delete;

  size_t max_size() const { return maxSize; }

  size_t read(T *dest, size_t size) {
    size_t sizeToCopy = 0;
    size_t remainingSize = 0;
    {
      std::lock_guard<std::mutex> lock(m);
      auto availableData = data.size();
      if (availableData > 0) {
        sizeToCopy = std::min(availableData, size);
        std::copy_n(data.begin(), sizeToCopy, dest);
        data.erase(data.begin(), data.begin() + sizeToCopy);
      }
      remainingSize = data.size();
    }

    if (remainingSize == 0) {
      onEmptyCallback(*this);
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

  size_t waitForData(std::stop_token stopToken = std::stop_token(),
                     size_t size = 1) {
    if (size > 0) {
      std::unique_lock<std::mutex> lock(m);
      hasDataCon.wait(lock, stopToken, [this, &size] {
        return done || data.size() >= std::min(size, maxSize) || eof.load();
      });
    }

    return data.size();
  }

  size_t waitForDataFor(std::stop_token stopToken = std::stop_token(),
                        std::chrono::milliseconds timeout = {},
                        size_t size = 1) {
    if (size > 0) {
      std::unique_lock<std::mutex> lock(m);
      hasDataCon.wait_for(lock, stopToken, timeout, [this, &size] {
        return done || data.size() >= std::min(size, maxSize) || eof.load();
      });
    }

    return data.size();
  }

  size_t waitForSpace(std::stop_token stopToken = std::stop_token(),
                      size_t size = 1) {
    if (maxSize != 0) {
      size = std::min(size, maxSize);
      if (size > 0) {
        std::unique_lock<std::mutex> lock(m);
        hasSpaceCon.wait(lock, stopToken, [this, &size]() {
          return done || (maxSize - data.size()) >= size;
        });
      }
      return maxSize - data.size();
    }

    return std::numeric_limits<size_t>::max();
  }

  size_t size() const {
    std::lock_guard lock(m);
    return data.size();
  }

  size_t availableSpace() const {
    std::lock_guard lock(m);
    if (maxSize == 0) {
      return std::numeric_limits<size_t>::max();
    }
    return maxSize - data.size();
  }

  bool empty() const { return data.empty(); }

  void setEof() {
    eof.store(true);
    {
      std::lock_guard<std::mutex> lock(m);
      if (data.empty()) {
        onEmptyCallback(*this);
      }
    }
    hasDataCon.notify_all();
  }

  bool isEof() { return eof.load(); }

  void resetEof() { eof.store(false); }

  void clear() {
    {
      std::lock_guard<std::mutex> lock(m);
      data.clear();
      if (data.empty()) {
        onEmptyCallback(*this);
      }
    }
    hasSpaceCon.notify_all();
  }
};

#endif