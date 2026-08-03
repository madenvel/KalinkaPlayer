#pragma once

#include <cstdint>

/// Version this binary speaks, offered as both min and max in Hello.
constexpr uint32_t kRendererProtocolVersion = 1;

/// mDNS TXT key a Core advertises its version under; no match, no connection.
constexpr const char *kRendererProtoTxtKey = "renderer_proto";
