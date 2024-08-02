#ifndef UTILS_H
#define UTILS_H

#include <functional>
#include <thread>

struct CombinedStopToken {
  std::stop_source stopSource;
  std::stop_callback<std::function<void()>> callback1;
  std::stop_callback<std::function<void()>> callback2;

  CombinedStopToken(const std::stop_token &token1,
                    const std::stop_token &token2)
      : stopSource(),
        callback1(token1, [this]() { stopSource.request_stop(); }),
        callback2(token2, [this]() { stopSource.request_stop(); }) {}

  CombinedStopToken() = delete;

  std::stop_token get_token() const { return stopSource.get_token(); }
};

inline CombinedStopToken combineStopTokens(const std::stop_token &token1,
                                           const std::stop_token &token2) {

  return CombinedStopToken(token1, token2);
}

template <typename SignalType> class Signal {
private:
  std::stop_source signalStopSource;
  std::condition_variable_any cv;
  mutable std::mutex mutex;

  std::optional<SignalType> value;
  SignalType ackValue;

public:
  Signal() = default;
  ~Signal() { cv.notify_all(); }

  void sendValue(const SignalType &value) {
    std::unique_lock lock(mutex);
    this->value = value;
    signalStopSource.request_stop();
  }

  std::optional<SignalType> getValue() const { return value; }

  SignalType getResponse(std::stop_token stopToken) {
    std::unique_lock lock(mutex);
    cv.wait(lock, stopToken, [this] { return !value.has_value(); });
    return ackValue;
  }

  void respond(const SignalType &ackValue) {
    std::unique_lock lock(mutex);
    value.reset();
    this->ackValue = ackValue;
    signalStopSource = std::stop_source();
    lock.unlock();
    cv.notify_all();
  }

  std::stop_token getStopToken() const {
    std::lock_guard lock(mutex);
    return signalStopSource.get_token();
  }
};

#endif