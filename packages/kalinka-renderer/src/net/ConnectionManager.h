#pragma once

#include <boost/asio.hpp>

#include <map>
#include <memory>
#include <string>

#include "../Identity.h"
#include "../discovery/Discovery.h"
#include "CoreConnection.h"

/**
 * @brief Owns one CoreConnection per discovered Core, keyed by
 *        CoreEndpoint::key.
 *
 * @note Every method is safe to call from any thread — discovery runs on its
 *       own — because each one posts onto the io_context and does its work
 *       there.
 */
class ConnectionManager {
public:
  ConnectionManager(boost::asio::io_context &ioc, Identity identity,
                    std::string friendlyName, RendererServices services);

  /**
   * @brief Connect to a Core, if it is not already connected.
   *
   * An endpoint whose key is already connected is ignored.
   */
  void add(CoreEndpoint endpoint);

  /** Replace a Core's route without sending a renderer-shutdown Goodbye. */
  void replace(CoreEndpoint endpoint);

  /// Stop and forget one Core. Unknown keys are ignored.
  void remove(std::string key);

  /// Stop every connection and refuse further ones. Idempotent.
  void stop();

private:
  void connect(CoreEndpoint endpoint);

  boost::asio::io_context &ioc_;
  Identity identity_;
  std::string friendlyName_;
  RendererServices services_;
  std::map<std::string, std::shared_ptr<CoreConnection>> connections_;
  bool stopped_ = false;
};
