#pragma once

#include <memory>

#include "config/ConfigService.h"
#include "session/SessionManager.h"

/**
 * @brief The two planes a Core connection talks to, shared by every connection.
 *
 * The session plane decides who owns the audio graph and carries playback;
 * the config plane says how the renderer is set up and needs no session.
 * Bundled so that adding a plane does not re-thread every constructor between
 * main() and CoreConnection.
 */
struct RendererServices {
  std::shared_ptr<SessionManager> sessions;
  std::shared_ptr<ConfigService> config;
};
