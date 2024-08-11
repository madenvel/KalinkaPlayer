#ifndef CONFIG_H
#define CONFIG_H

#include "Log.h"
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

#endif