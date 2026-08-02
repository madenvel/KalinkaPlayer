#pragma once

#include <string>

// Stable renderer identity plus per-process instance id.
//
// renderer_id persists at <KALINKA_PREFIX>/var/lib/kalinka-renderer/renderer_id
// (KALINKA_PREFIX defaults to "/", same convention as the server's paths.py).
// If the state directory is not writable a warning is logged and an ephemeral
// id is used for this run.
struct Identity {
  std::string rendererId;
  std::string instanceId;

  static Identity load();
};

std::string generateUuid();
