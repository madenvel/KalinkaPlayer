#pragma once

#include <boost/asio.hpp>

#include <memory>
#include <string>

#include "../Identity.h"
#include "../RendererServices.h"
#include "../discovery/Discovery.h"
#include "ProtocolSession.h"
#include "WsTransport.h"

/**
 * @brief One Core, assembled: a WsTransport carrying a ProtocolSession.
 *
 * Nothing but wiring — the transport moves bytes and reconnects, the protocol
 * decides what they mean. The two hold each other weakly through their
 * callbacks, so owning this object is what keeps the pair alive.
 */
class CoreConnection {
public:
  /**
   * @param identity Outlives this connection; every reconnect re-announces it.
   * @param services The session and config planes, shared with every other
   *                 connection.
   */
  CoreConnection(boost::asio::io_context &ioc, CoreEndpoint endpoint,
                 const Identity &identity, std::string friendlyName,
                 RendererServices services);

  /// Begin connecting. Nothing happens until this is called.
  void start();

  /**
   * @brief Graceful shutdown: best-effort Goodbye, then a WebSocket close,
   *        bounded by the transport's 2s deadline. Idempotent.
   *
   * @note Must be called on the io_context thread.
   */
  void stop();

private:
  std::shared_ptr<ProtocolSession> protocol_;
  std::shared_ptr<WsTransport> transport_;
};
