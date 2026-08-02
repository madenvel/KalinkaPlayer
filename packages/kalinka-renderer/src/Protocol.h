#pragma once

#include <cstdint>

// Version this binary speaks; servers advertise theirs in the mDNS TXT key
// "renderer_proto" and renderers only connect on a match.
constexpr uint32_t kRendererProtocolVersion = 1;
constexpr const char *kRendererProtoTxtKey = "renderer_proto";
