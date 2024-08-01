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

#endif