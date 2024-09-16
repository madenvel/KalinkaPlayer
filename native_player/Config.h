#ifndef CONFIG_H
#define CONFIG_H

#include <boost/lexical_cast.hpp>
#include <string>
#include <unordered_map>

using Config = std::unordered_map<std::string, std::string>;

template <class T>
inline T value_or(const Config &config, const std::string &key,
                  const T &default_value) {
  return config.contains(key) ? boost::lexical_cast<T>(config.at(key))
                              : default_value;
}

template <class T>
inline std::optional<T> value(const Config &config, const std::string &key) {
  return config.contains(key)
             ? std::optional<T>(boost::lexical_cast<T>(config.at(key)))
             : std::nullopt;
}

template <>
inline bool value_or<bool>(const Config &config, const std::string &key,
                           const bool &default_value) {
  if (config.contains(key)) {
    const std::string &value = config.at(key);
    if (value == "true") {
      return true;
    } else if (value == "false") {
      return false;
    }
  }
  return default_value;
}

template <>
inline std::optional<bool> value<bool>(const Config &config,
                                       const std::string &key) {
  if (config.contains(key)) {
    const std::string &value = config.at(key);
    if (value == "true") {
      return true;
    } else if (value == "false") {
      return false;
    }
  }
  return std::nullopt;
}

#endif