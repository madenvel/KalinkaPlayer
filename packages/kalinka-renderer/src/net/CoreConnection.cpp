#include "CoreConnection.h"

CoreConnection::CoreConnection(boost::asio::io_context &ioc,
                               CoreEndpoint endpoint, const Identity &identity,
                               std::string friendlyName,
                               RendererServices services) {
  protocol_ = std::make_shared<ProtocolSession>(
      endpoint.name, identity, std::move(friendlyName), std::move(services));
  transport_ = std::make_shared<WsTransport>(ioc, std::move(endpoint));

  transport_->bind({
      .onUp = [p = std::weak_ptr(protocol_)] {
        if (auto protocol = p.lock()) protocol->onUp();
      },
      .onMessage = [p = std::weak_ptr(protocol_)](const std::string &data) {
        if (auto protocol = p.lock()) protocol->onMessage(data);
      },
      .onDown = [p = std::weak_ptr(protocol_)] {
        if (auto protocol = p.lock()) protocol->onDown();
      },
  });
  protocol_->bind({
      .send = [t = std::weak_ptr(transport_)](std::string data) {
        auto transport = t.lock();
        return transport && transport->send(std::move(data));
      },
      .giveUp = [t = std::weak_ptr(transport_)] {
        if (auto transport = t.lock()) transport->giveUp();
      },
  });
}

void CoreConnection::start() { transport_->start(); }

void CoreConnection::stop() { transport_->stop(protocol_->shutdownFrame()); }
