# Standalone native renderer — design proposal

Status: **proposal** — no code yet. Stage 1 deliverable; awaiting review of the
protocol, package structure, dependencies and build targets before any
implementation starts.

Scope: a new `packages/kalinka-renderer/` package (modern C++, no Python
runtime) that reuses the existing C++ audio graph and connects to Kalinka Core
over a persistent WebSocket carrying binary Protocol Buffers. Core and its play
queue stay Python. Minimal, additive changes land in `packages/kalinka-server`.

Out of scope: multi-room synchronization (the protocol advertises it as
unsupported and nothing more), authentication/pairing, and the web renderer
implementation itself.

---

## 1. Repository findings

### 1.1 `AudioPlayer` API and implementation

| Item | Location |
|---|---|
| Public API | `packages/kalinka-server/src/native_player/AudioPlayer.h` |
| Implementation | `packages/kalinka-server/src/native_player/AudioPlayer.cpp` |
| Python binding | `packages/kalinka-server/src/native_player/PyBindings.cpp:131-146` |
| Sole consumer | `packages/kalinka-server/src/kalinka_server/playqueue.py:229` (`PlayQueueImpl`) |

```cpp
StreamId append(const std::string &url, AudioFormat format = FormatFlac);
void     remove(StreamId id);
void     clearAll();
void     stop();
void     pause();
void     resume();
size_t   seek(size_t positionMs);
StreamState getState();
std::unique_ptr<StateMonitor> monitor();
void configureVolume(const std::string &mode, const std::string &mixerControl);
VolumeState getVolume();
void setVolume(int percent);
std::unique_ptr<VolumeMonitor> volumeMonitor();
```

Semantics that shape the protocol (`AudioPlayer.cpp:215-245`):

- `append()` builds a node chain — `FileInputNode` | `AudioGraphHttpStream` |
  `SineWaveNode` (for `tone://`) → decoder (`FlacStreamDecoder` /
  `Mp3StreamDecoder`) → `AudioStreamSwitcher` → `AlsaAudioEmitter`. Playback
  **auto-starts**; there is no explicit `play()`.
- Gapless is two streams appended simultaneously; `AudioStreamSwitcher` emits
  `SOURCE_CHANGED` at the crossover.
- `clearAll()` keeps the device open; `stop()` closes it.
- Volume mode defaults to `Fixed` (no processing, bit-perfect) until
  `configureVolume()` is called.

### 1.2 Commands, states, callbacks, events

`StreamState.h`, `AudioInfo.h`, `StateMonitor.h`:

- `AudioGraphNodeState`: `ERROR = -1, STOPPED, PREPARING, STREAMING, PAUSED,
  FINISHED, SOURCE_CHANGED`
- `StreamState { state, position (long, ms), optional<StreamInfo>,
  optional<StreamError>, timestamp (steady_clock ns) }`
- `StreamInfo { StreamAudioFormat format{sampleRate, channels, bitsPerSample,
  sampleFormat}, StreamType{BYTES|FRAMES}, streamSize }`
- `StreamError { StreamErrorSource{NONE|HTTP_STREAM|AUDIO_OUTPUT|DECODER},
  message }`
- Delivery: `AudioGraphNode::onStateChange(callback)` internally; externally a
  **blocking pull queue** via `StateMonitor::waitState()`. Python drives it on a
  thread pool (`playqueue.py:170-218`).
- The Core's interpretation lives in `_process_state_update`
  (`playqueue.py:319-414`) and `to_state_name` (`playqueue.py:145`). Note that
  `SOURCE_CHANGED` maps to `None` and returns early — it is a control-flow
  signal, not a reported state.

### 1.3 Volume

`AlsaVolumeControl.h`: `VolumeBackend{None,Hardware,Software}`,
`VolumeMode{Auto,Hardware,Software,Fixed}`,
`VolumeState{supported, current, max, backend}` on a 0..100 scale.
`VolumeMonitor::wait()` blocks for **external** mixer changes (hardware knob,
`amixer`, another app).

Bridged to Core by `kalinka_server/alsa_volume_device.py` as the built-in
`local-alsa` `ExternalOutputDevice`, constructed through
`PlayQueueImpl.create_volume_control_device` (`playqueue.py:281-295`).

### 1.4 Device enumeration and selection

`AlsaDeviceEnumeration.h`: `AlsaPcmDevice { name, label, ioid }`,
`listAlsaPcmDevices()`. The concrete device identifier is the **PCM name**
passed to `snd_pcm_open` — `default`, `hw:CARD=sndrpihifiberry,DEV=0`,
`plughw:…`. UI filtering lives in `kalinka_server/alsa_options.py`.

**Selection is construction-time only.** `AlsaAudioEmitter` reads
`output.alsa.device` from the `Config` map in its constructor and `AudioPlayer`
is built once, so there is no runtime device-switch path today.

### 1.5 Audio graph and ALSA sink

`AudioGraphNode.h` (base / `OutputNode` / `InputNode` / `EmitterNode`),
`AudioStreamSwitcher.h`, `AlsaAudioEmitter.h` (owns the real-time
`playbackThread`), `Buffer.h`, `FlacStreamDecoder.h`, `Mp3StreamDecoder.h`,
`AudioGraphHttpStream.h`, `FileInputNode.h`.

**Key enabler:** `pybind11` appears in the graph only behind `#ifdef __PYTHON__`
— two `gil_scoped_release` sites, at `StateMonitor.cpp:22` and
`AlsaVolumeControl.cpp:356`. `native_player/tests/Makefile` already compiles the
whole graph **without** `-D__PYTHON__`, so a pybind11-free static library of the
audio graph is already proven to build.

### 1.6 Native build configuration

- `native_player/setup.py`: `--std=c++23 -fpermissive -O2 -D__PYTHON__`;
  libraries `curlpp curl FLAC++ FLAC asound pthread spdlog fmt`; Boost headers
  via `Config.h` (`boost::lexical_cast`).
- `native_player/tests/Makefile`: g++, gtest/gmock, `-fsanitize=address`.
- Root `Makefile` orchestrates (`build-native`, `dev-setup`, `dev-run`,
  `*-deb`).
- Deb: `packages/kalinka-server/scripts/build_deb.sh`,
  `packages/kalinka-server/DEBIAN/control.in` (arch- and platform-suffixed,
  `@SHLIBDEPS@`).
- CI: `.github/workflows/release.yml` — arm64 in `debian:trixie`, amd64 in
  `ubuntu:24.04`, native runners, no QEMU.

**There is no CMake anywhere in the repository today.**

### 1.7 Zeroconf / mDNS

`kalinka_server/service_discovery.py`:

- Service type `_kalinkaplayer._tcp.local.`
- Instance name `{config.server.service_name}._kalinkaplayer._tcp.local.` —
  default `"My Kalinka Service"`, user-editable
- TXT: `kalinka_api_version` (= `REST_API_VERSION`, `"0.1"`), `server_version`
- Port `config.server.port` (default 8000); a single instance carrying one A
  record per interface

**There is no stable server identity today** — no UUID, no machine-id, nothing
in TXT. The instance name is a mutable display string. Closing this gap is a
prerequisite for renderer-side deduplication.

### 1.8 Python WebSocket endpoints and player abstractions

- `@app.websocket("/queue/ws")` → `kalinka_server/queue_ws_handler.py` — JSON,
  pydantic discriminated union on `command`, event stream out.
- `@app.websocket("/device/ws")` → `kalinka_server/device_ws_handler.py` — same
  shape for `ExternalOutputDevice`.
- Abstractions: `PlayQueueController`
  (`kalinka_plugin_sdk/api.py`), `ExternalOutputDevice`
  (`kalinka_plugin_sdk/ext_device.py`).

`ExternalOutputDevice` is a **volume/power** device abstraction, not a renderer
abstraction. There is no existing seam for "something else does the audio".

### 1.9 Existing Protobuf usage

**None.** No `.proto` files, no `protobuf` dependency, no gRPC anywhere. The
only match for "protobuf" is a comment about ONNX model file size. Everything is
JSON/pydantic.

Consequently there is **no compelling reason to introduce gRPC**: it would add a
large runtime to a 512 MB-RAM target, requires HTTP/2 (the discovery record
advertises a plain HTTP port), and its bidirectional streaming would duplicate
the WebSocket transport already used by `/queue/ws` and `/device/ws`.

### 1.10 Web client technology

The browser player is the optional `kalinka-web` deb, served from
`paths.web_ui_dir()` = `/usr/share/kalinka-web` (`kalinka_server/web_ui.py`,
`kalinka_plugin_sdk/paths.py:30`). Per `scripts/install-release.sh:152-162` it is
built and released from the **app repo `madenvel/KalinkaAI`** — i.e. it is the
Flutter app's web build.

A future web renderer therefore targets **Dart** (`protoc_plugin`), not
TypeScript.

---

## 2. Proposed architecture

```
 ┌──────────────────── kalinka-renderer (C++, one process) ─────────────────────┐
 │                                                                              │
 │  [Discovery thread]     [Network thread: one asio io_context]  [Control thr] │
 │  Avahi browse      ──▶  ConnectionManager                                    │
 │  _kalinkaplayer._tcp     ├── Session(core A) ─┐                              │
 │  dedupe by server_id     ├── Session(core B) ─┤ bounded MPSC ─▶ PlayerAgent  │
 │  StaticDiscovery (cfg)   └── backoff timers   │ command queue   owns         │
 │                          protobuf parse/emit  │                 AudioPlayer  │
 │                                 ▲             │                    │         │
 │                      asio::post │             │                    ▼         │
 │                                 │             │           ┌──────────────┐   │
 │  [StateMonitor pump] ───────────┘             │           │ AudioPlayer  │   │
 │  [VolumeMonitor pump] ─────────────────────────┘           │ └ AlsaAudio- │   │
 │   blocking waitState() / wait()                            │   Emitter    │   │
 │                                                            │   (RT thread)│   │
 │                                                            └──────────────┘   │
 └──────────────────────────────────────────────────────────────────────────────┘
```

Responsibility boundaries:

- **`kalinka_audiograph`** (unchanged sources, new static-lib target) —
  decoding, ALSA, the real-time thread. Knows nothing about protobuf,
  networking, or Cores.
- **`kalinka_renderer_proto`** — generated protobuf-lite C++. No other
  dependencies.
- **`kalinka_renderer_core`** — discovery, transport, session state machine,
  arbitration, player agent, translation. The testable unit.
- **`kalinka-renderer`** — thin `main()`: parse config, build identity, wire the
  three together, install signal handlers.

The renderer is **not** a queue. It holds at most a current source plus
prefetched sources, mirroring `AudioPlayer`'s stream list exactly. Queue,
playback modes, track metadata and prefetch policy stay in Python Core.

---

## 3. Proposed file tree

```text
packages/kalinka-renderer/
├── CMakeLists.txt                  # options: BUILD_TESTING, ENABLE_ASAN, USE_SYSTEM_AVAHI
├── README.md
├── cmake/
│   ├── FindAvahi.cmake
│   └── ProtobufCodegen.cmake       # protoc invocation shared by C++ and `make proto`
├── proto/
│   └── kalinka/renderer/v1/renderer.proto
├── config/
│   └── renderer.example.conf
├── src/
│   ├── main.cpp
│   ├── RendererConfig.{h,cpp}      # dotted key=value -> Config (unordered_map<string,string>)
│   ├── Identity.{h,cpp}            # renderer_id persistence + per-process instance_id
│   ├── discovery/
│   │   ├── Discovery.h             # IDiscovery, CoreEndpoint, listener interface
│   │   ├── AvahiDiscovery.{h,cpp}
│   │   └── StaticDiscovery.{h,cpp} # endpoints from config; used by tests and Avahi-less hosts
│   ├── net/
│   │   ├── Transport.h             # ITransport (send/close/callbacks) — fake-able
│   │   ├── BeastWebSocket.{h,cpp}
│   │   ├── ConnectionManager.{h,cpp}
│   │   └── Backoff.{h,cpp}
│   ├── session/
│   │   ├── ProtocolSession.{h,cpp} # Hello/Welcome/command routing; transport-agnostic
│   │   ├── Arbiter.{h,cpp}         # ownership lease across Cores
│   │   └── SnapshotBuilder.{h,cpp}
│   ├── player/
│   │   ├── IPlayer.h               # narrow interface over AudioPlayer — fake-able
│   │   ├── NativePlayer.{h,cpp}    # IPlayer -> AudioPlayer
│   │   ├── PlayerAgent.{h,cpp}     # owns IPlayer, runs the control thread
│   │   ├── CommandQueue.h          # bounded MPSC, reject-on-full
│   │   ├── StateTranslator.{h,cpp} # StreamState / VolumeState -> proto
│   │   └── SourceTable.{h,cpp}     # source_token <-> StreamId
│   ├── platform/
│   │   ├── Platform.h              # os/arch/hostname/default friendly name
│   │   ├── PlatformLinux.cpp
│   │   └── OutputDevices.{h,cpp}   # wraps listAlsaPcmDevices() -> proto OutputDevice
│   └── util/Uuid.{h,cpp}
├── tests/
│   ├── CMakeLists.txt
│   ├── fakes/{FakePlayer.h,FakeTransport.h,FakeDiscovery.h}
│   ├── ProtocolSession_test.cpp
│   ├── StateTranslator_test.cpp
│   ├── CommandQueue_test.cpp
│   ├── Arbiter_test.cpp
│   ├── Backoff_test.cpp
│   ├── Identity_test.cpp
│   ├── OutputDevices_test.cpp
│   └── interop/
│       ├── emit_golden.cpp         # writes golden serialized envelopes
│       └── test_interop.py         # pytest: parses them with the Python bindings, and vice versa
└── packaging/
    ├── DEBIAN/control.in
    ├── kalinka-renderer.service
    ├── kalinka-renderer.tmpfiles.conf
    └── build_deb.sh
```

Additive changes in the **existing server package**:

```text
packages/kalinka-server/src/native_player/
├── CMakeLists.txt                  # NEW: kalinka_audiograph static lib (no -D__PYTHON__)
└── SOURCES.txt                     # NEW: single source list, read by BOTH setup.py and CMake

packages/kalinka-server/src/kalinka_server/
├── server_identity.py              # NEW: persisted server_id UUID
├── renderer_ws_handler.py          # NEW: binary protobuf WS handler
├── renderer_registry.py            # NEW: connected-renderer registry
└── renderer_proto/                 # NEW: committed generated *_pb2.py
```

### 3.1 Linking to the existing audio graph

No duplication and no move. `packages/kalinka-renderer/CMakeLists.txt` does:

```cmake
add_subdirectory(${CMAKE_SOURCE_DIR}/../kalinka-server/src/native_player audiograph)
target_link_libraries(kalinka_renderer_core PUBLIC kalinka_audiograph)
```

The new `native_player/CMakeLists.txt` reads `SOURCES.txt` (excluding
`PyBindings.cpp`) and compiles with `-std=c++23 -fpermissive` and **no**
`-D__PYTHON__`. `setup.py` is changed to read the same `SOURCES.txt` so the two
lists cannot drift. That is the only edit to an existing build file.

---

## 4. Draft `.proto` schema

```protobuf
// packages/kalinka-renderer/proto/kalinka/renderer/v1/renderer.proto
syntax = "proto3";

package kalinka.renderer.v1;

option optimize_for = LITE_RUNTIME;

// ============================================================================
// Transport contract
//
// One Envelope per binary WebSocket message. Endpoint: ws://<core>/renderer/ws
// Liveness uses WebSocket control frames (ping/pong); there is deliberately no
// application-level heartbeat. See §7.
// ============================================================================

message Envelope {
  // Monotonic from 1, per connection, per direction. Resets on reconnect.
  uint64 message_id = 1;

  oneof payload {
    // ---- renderer -> core -------------------------------------------------
    Hello                  hello                    = 1000;
    StateSnapshot          state_snapshot           = 1001;
    PlaybackStateChanged   playback_state_changed   = 1002;
    SourceChanged          source_changed           = 1003;
    AudioFormatChanged     audio_format_changed     = 1004;
    VolumeChanged          volume_changed           = 1005;
    OutputDevicesChanged   output_devices_changed   = 1006;
    SelectedDeviceChanged  selected_device_changed  = 1007;
    PlaybackError          playback_error           = 1008;
    CommandResult          command_result           = 1009;
    CapabilitiesChanged    capabilities_changed     = 1010;
    OwnershipChanged       ownership_changed        = 1011;
    SessionOpenResult      session_open_result      = 1012;   // implemented
    SessionClosed          session_closed           = 1014;   // implemented

    // ---- core -> renderer -------------------------------------------------
    Welcome                welcome                  = 2000;
    Command                command                  = 2001;
    SessionOpen            session_open             = 2003;   // implemented
    SessionClose           session_close            = 2004;   // implemented

    // ---- either direction -------------------------------------------------
    Goodbye                goodbye                  = 3000;
  }

  // 1015-1099 / 2005-2099 held for future renderer/core messages.
  // 1013 was considered for an application-level Pong and is NOT used;
  // 2002 likewise for Ping. Do not reuse without a version bump.
  reserved 1013, 2002;
}

// ============================================================================
// Versioning
// ============================================================================

message ProtocolVersionRange {
  uint32 min = 1;   // lowest version the sender can speak
  uint32 max = 2;   // highest version the sender can speak
}

// ============================================================================
// Enums. Values are named domain constants; ordinals carry no meaning and are
// never mapped positionally onto AudioGraphNodeState or any other native enum.
// ============================================================================

enum PlaybackState {
  PLAYBACK_STATE_UNSPECIFIED = 0;
  PLAYBACK_STATE_STOPPED     = 1;   // AudioGraphNodeState::STOPPED
  PLAYBACK_STATE_PREPARING   = 2;   // AudioGraphNodeState::PREPARING
  PLAYBACK_STATE_PLAYING     = 3;   // AudioGraphNodeState::STREAMING
  PLAYBACK_STATE_PAUSED      = 4;   // AudioGraphNodeState::PAUSED
  PLAYBACK_STATE_FINISHED    = 5;   // AudioGraphNodeState::FINISHED (end of source)
  PLAYBACK_STATE_ERROR       = 6;   // AudioGraphNodeState::ERROR
  // SOURCE_CHANGED is deliberately absent: it is a transition, delivered as
  // the SourceChanged message. This matches the Core, which already returns
  // None from to_state_name() for it and handles it as control flow.
}

enum ErrorSource {
  ERROR_SOURCE_UNSPECIFIED       = 0;
  ERROR_SOURCE_NONE              = 1;
  ERROR_SOURCE_HTTP_STREAM       = 2;
  ERROR_SOURCE_AUDIO_OUTPUT      = 3;
  ERROR_SOURCE_DECODER           = 4;
  ERROR_SOURCE_RENDERER_INTERNAL = 5;   // no native analogue; renderer-side faults
}

enum StreamKind {                        // native StreamType
  STREAM_KIND_UNSPECIFIED = 0;
  STREAM_KIND_BYTES       = 1;
  STREAM_KIND_FRAMES      = 2;
}

enum VolumeBackend {                     // native VolumeBackend
  VOLUME_BACKEND_UNSPECIFIED = 0;
  VOLUME_BACKEND_NONE        = 1;
  VOLUME_BACKEND_HARDWARE    = 2;
  VOLUME_BACKEND_SOFTWARE    = 3;
}

enum RendererKind {
  RENDERER_KIND_UNSPECIFIED = 0;
  RENDERER_KIND_NATIVE      = 1;
  RENDERER_KIND_WEB         = 2;
}

enum ControlKind {
  CONTROL_KIND_UNSPECIFIED          = 0;
  CONTROL_KIND_SET_SOURCE           = 1;
  CONTROL_KIND_ENQUEUE_SOURCE       = 2;
  CONTROL_KIND_REMOVE_SOURCE        = 3;
  CONTROL_KIND_CLEAR_QUEUE          = 4;
  CONTROL_KIND_PLAY                 = 5;
  CONTROL_KIND_PAUSE                = 6;
  CONTROL_KIND_STOP                 = 7;
  CONTROL_KIND_SET_VOLUME           = 8;
  CONTROL_KIND_REQUEST_SNAPSHOT     = 9;
  CONTROL_KIND_REFRESH_DEVICES      = 10;
  CONTROL_KIND_SELECT_OUTPUT_DEVICE = 11;
  CONTROL_KIND_RELEASE_CONTROL      = 12;
  CONTROL_KIND_SEEK                 = 13;
}

// ============================================================================
// Value types
// ============================================================================

// Decoded PCM description. Mirrors native StreamInfo / StreamAudioFormat.
// stream_kind + stream_size_units are carried verbatim so the Core can reuse
// its existing get_duration_ms() unchanged.
message AudioFormat {
  uint32     sample_rate_hz    = 1;
  uint32     channels          = 2;
  uint32     bits_per_sample   = 3;
  string     sample_format     = 4;   // sampleFormatToString(), e.g. "S24_LE"
  StreamKind stream_kind       = 5;
  uint64     stream_size_units = 6;   // bytes or frames per stream_kind
}

message Source {
  string uri          = 1;            // http(s)://, file://, tone://
  string mime_type    = 2;            // "audio/flac", "audio/mpeg" — renderer maps to a decoder
  string source_token = 3;            // Core-assigned; opaque to the renderer, echoed back
}

message VolumeState {
  bool          supported = 1;
  uint32        current   = 2;        // 0..max
  uint32        max       = 3;        // 100 for the ALSA backend
  VolumeBackend backend   = 4;
}

message ErrorInfo {
  ErrorSource     source       = 1;
  string          message      = 2;
  optional string source_token = 3;   // the source that faulted, when attributable
}

// --- Output devices ---------------------------------------------------------
// `id` is opaque at the protocol level. Backend-specific detail is confined to
// the `backend` oneof so ALSA fields never become mandatory for WASAPI,
// Core Audio, or a browser renderer.
message OutputDevice {
  string id          = 1;
  string label       = 2;
  string description = 3;
  bool   is_default  = 4;
  bool   is_selected = 5;

  oneof backend {
    AlsaDevice      alsa       = 100;
    WasapiDevice    wasapi     = 101;   // reserved shape, not implemented
    CoreAudioDevice core_audio = 102;   // reserved shape, not implemented
    WebAudioDevice  web_audio  = 103;   // reserved shape, not implemented
  }
}

message AlsaDevice {
  string pcm_name = 1;   // exactly what is passed to snd_pcm_open()
  string ioid     = 2;   // "Output" | "Input" | "" (both)
  string card_id  = 3;   // parsed CARD=<id>, when present
}
message WasapiDevice    { string endpoint_id = 1; }
message CoreAudioDevice { string device_uid  = 1; }
message WebAudioDevice  { string sink_id     = 1; }   // MediaDeviceInfo.deviceId (origin-scoped)

// --- Capabilities -----------------------------------------------------------
message VolumeCapabilities {
  bool          supported                = 1;
  uint32        max                      = 2;
  VolumeBackend backend                  = 3;
  bool          reports_external_changes = 4;   // hardware mixer monitoring
}

message Capabilities {
  repeated string    supported_uri_schemes = 1;   // e.g. ["http","https","file","tone"]
  repeated string    supported_mime_types  = 2;   // e.g. ["audio/flac","audio/mpeg"]
  bool               gapless               = 3;
  bool               seek                  = 4;   // false in the first implementation
  bool               multiroom_sync        = 5;   // always false; advertised as unsupported
  bool               device_selection      = 6;
  bool               queue_prefetch        = 7;   // enqueue/remove supported
  uint32             max_queued_sources    = 8;
  VolumeCapabilities volume                = 9;
}

message Platform {
  string os            = 1;   // "linux"
  string os_version    = 2;
  string arch          = 3;   // "aarch64", "x86_64"
  string hostname      = 4;
  string audio_backend = 5;   // "alsa"
}

message OwnershipInfo {
  bool   held                = 1;
  string owner_server_id     = 2;
  string owner_server_name   = 3;
  int64  acquired_at_unix_ms = 4;
}

// ============================================================================
// Complete state snapshot.
// One definition, used by Hello, by RequestSnapshot, and after reconnection.
// ============================================================================
message StateSnapshot {
  PlaybackState          playback_state       = 1;
  optional Source        current_source       = 2;
  optional AudioFormat   format               = 3;

  // Position already advanced to send time by the renderer. The native
  // StreamState.timestamp is a steady_clock value local to the renderer
  // process and is deliberately NOT put on the wire.
  uint64                 position_ms          = 4;
  bool                   position_valid       = 5;
  int64                  captured_at_unix_ms  = 6;

  VolumeState            volume               = 7;
  repeated OutputDevice  output_devices       = 8;
  optional string        selected_device_id   = 9;
  optional ErrorInfo     error                = 10;

  // Prefetched sources not yet playing, in switch order. Empty when the
  // renderer holds only the current source.
  repeated string        queued_source_tokens = 11;
  optional OwnershipInfo ownership            = 12;
}

// ============================================================================
// Handshake
// ============================================================================

message Hello {
  ProtocolVersionRange  protocol_versions  = 1;
  string                renderer_id        = 2;   // stable across reboots/upgrades
  string                instance_id        = 3;   // fresh per process
  string                friendly_name      = 4;
  string                software_version   = 5;
  RendererKind          kind               = 6;
  Platform              platform           = 7;
  Capabilities          capabilities       = 8;
  repeated ControlKind  supported_controls = 9;
  StateSnapshot         state              = 10;  // complete current state
}

message Welcome {
  uint32                 protocol_version    = 1;   // the version the Core selected
  string                 server_id           = 2;   // stable Core identity
  string                 server_instance_id  = 3;
  string                 server_name         = 4;   // config.server.service_name
  string                 server_version      = 5;
  string                 api_version         = 6;   // REST_API_VERSION
  int64                  server_time_unix_ms = 7;
  optional OwnershipInfo ownership           = 8;   // Core's view at accept time
}

message Goodbye {
  enum Reason {
    REASON_UNSPECIFIED         = 0;
    REASON_SHUTDOWN            = 1;
    REASON_VERSION_UNSUPPORTED = 2;
    REASON_MALFORMED           = 3;
    REASON_REPLACED            = 4;   // same renderer_id reconnected elsewhere
    REASON_REJECTED            = 5;
  }
  Reason reason = 1;
  string detail = 2;
}

// ============================================================================
// Core -> renderer commands
// ============================================================================

message Command {
  uint64 command_id     = 1;   // Core-assigned, monotonic per connection
  bool   force_takeover = 2;   // arbitration override; see §6

  oneof op {
    SetSource            set_source             = 10;
    EnqueueSource        enqueue_source         = 11;
    RemoveSource         remove_source          = 12;
    ClearQueue           clear_queue            = 13;
    Play                 play                   = 14;
    Pause                pause                  = 15;
    Stop                 stop                   = 16;
    SetVolume            set_volume             = 17;
    RequestSnapshot      request_snapshot       = 18;
    RefreshOutputDevices refresh_output_devices = 19;
    SelectOutputDevice   select_output_device   = 20;
    ReleaseControl       release_control        = 21;
    Seek                 seek                   = 22;
  }
}

// Replace current playback with this source. Maps to AudioPlayer::append()
// plus removal of the previously-current stream — the Core's _apply_play().
message SetSource     { Source source = 1; }

// Prefetch: append alongside the current source for a gapless switch.
// Maps to AudioPlayer::append() — the Core's _apply_prefetch().
message EnqueueSource { Source source = 1; }

// Maps to AudioPlayer::remove(StreamId).
message RemoveSource  { string source_token = 1; }

// Maps to AudioPlayer::clearAll() — device stays open.
message ClearQueue    {}

// Resume from pause. There is no separate "start": AudioPlayer starts playing
// on append(), so Play on a stopped renderer with a current source re-appends it.
message Play          {}
message Pause         {}

// Maps to AudioPlayer::stop() — device closed.
message Stop          {}

message SetVolume            { uint32 percent = 1; }
message RequestSnapshot      {}
message RefreshOutputDevices {}

// Proposed extension. Rejected with REJECT_REASON_UNSUPPORTED unless
// Capabilities.device_selection is true.
message SelectOutputDevice { string device_id = 1; }

message ReleaseControl {}

// Capability-gated; rejected with REJECT_REASON_UNSUPPORTED while
// Capabilities.seek is false.
message Seek { uint64 position_ms = 1; }

// ============================================================================
// Renderer -> core responses and events
// ============================================================================

enum CommandStatus {
  COMMAND_STATUS_UNSPECIFIED = 0;
  COMMAND_STATUS_ACCEPTED    = 1;   // queued to the player; observe events for the effect
  COMMAND_STATUS_REJECTED    = 2;   // not attempted
  COMMAND_STATUS_FAILED      = 3;   // attempted, did not succeed
}

enum RejectReason {
  REJECT_REASON_UNSPECIFIED          = 0;
  REJECT_REASON_NOT_OWNER            = 1;
  REJECT_REASON_UNSUPPORTED          = 2;
  REJECT_REASON_INVALID_ARGUMENT     = 3;
  REJECT_REASON_UNKNOWN_SOURCE_TOKEN = 4;
  REJECT_REASON_QUEUE_FULL           = 5;
  REJECT_REASON_NOT_READY            = 6;
  REJECT_REASON_UNSUPPORTED_SCHEME   = 7;
}

// Acknowledgement that the command was accepted, rejected, or failed. This is
// NOT a statement that the requested state was reached — that arrives later as
// a PlaybackStateChanged / VolumeChanged / SelectedDeviceChanged event.
message CommandResult {
  uint64                command_id   = 1;
  CommandStatus         status       = 2;
  optional RejectReason reason       = 3;
  optional string       detail       = 4;
  optional string       source_token = 5;   // echoed for set_source / enqueue_source
  optional OwnershipInfo ownership   = 6;   // populated on REJECT_REASON_NOT_OWNER
}

message PlaybackStateChanged {
  PlaybackState   state          = 1;
  uint64          position_ms    = 2;   // advanced to send time
  bool            position_valid = 3;
  // The source this state refers to. Absent when the graph holds no current
  // source — which is how the Core distinguishes "the track ended" from
  // "the graph was torn down", a distinction it currently infers from
  // current_stream_id being None.
  optional string source_token   = 4;
  int64           at_unix_ms     = 5;
  optional ErrorInfo error       = 6;   // set when state == ERROR
}

// Emitted on AudioGraphNodeState::SOURCE_CHANGED — the gapless crossover.
message SourceChanged {
  string          source_token          = 1;   // the source now playing
  optional string previous_source_token = 2;
  int64           at_unix_ms            = 3;
}

message AudioFormatChanged {
  string      source_token = 1;
  AudioFormat format       = 2;
}

message VolumeChanged {
  VolumeState volume   = 1;
  bool        external = 2;   // true when it came from the hardware-mixer monitor
}

message OutputDevicesChanged  { repeated OutputDevice devices = 1; }
message SelectedDeviceChanged { optional string device_id = 1; }
message CapabilitiesChanged   {
  Capabilities         capabilities       = 1;
  repeated ControlKind supported_controls = 2;
}
message PlaybackError    { ErrorInfo error = 1; }
message OwnershipChanged { OwnershipInfo ownership = 1; }
```

### 4.1 Compatibility rules applied

- proto3 with explicit `optional` for presence.
- No enum ordinal is a domain value. Every enum has `UNSPECIFIED = 0` and named
  constants that do **not** mirror native numbering — note that native
  `AudioGraphNodeState::ERROR` is `-1`, which proto3 cannot express anyway.
- Envelope field numbers are banded by direction with gaps left in each band.
- `reserved 1013, 2002` records the two numbers considered and rejected for an
  application-level heartbeat, so they can never be silently reused.

---

## 5. Mapping: `AudioPlayer` API/events → protocol

| Existing method/event/state | Protocol message | Direction | Notes |
|---|---|---|---|
| `append(url, format)` — replace | `Command.set_source` | Core→R | `mime_to_format()` moves into the renderer; the wire carries a MIME string, not the native enum. Renderer returns `source_token` in `CommandResult`. |
| `append(url, format)` — prefetch | `Command.enqueue_source` | Core→R | Same native call; distinct message so the Core's `_apply_play` vs `_apply_prefetch` intent survives. Required for gapless. |
| `remove(StreamId)` | `Command.remove_source{source_token}` | Core→R | `SourceTable` maps token↔`StreamId`; the `StreamId` never leaves the renderer. |
| `clearAll()` | `Command.clear_queue` | Core→R | Device stays open. |
| `stop()` | `Command.stop` | Core→R | Device closed. |
| `pause()` | `Command.pause` | Core→R | |
| `resume()` | `Command.play` | Core→R | Named `Play` because that is the Core-facing verb; maps to `resume()`. On a stopped renderer holding a current source it re-appends. |
| `seek(ms)` | `Command.seek` | Core→R | Defined but capability-gated off (`Capabilities.seek = false`). See open question 6. |
| `getState()` | `Command.request_snapshot` → `StateSnapshot` | Core→R→Core | |
| `monitor()` / `StateMonitor::waitState()` | `PlaybackStateChanged`, `SourceChanged`, `AudioFormatChanged`, `PlaybackError` | R→Core | One native `StreamState` fans out into up to three messages. |
| `AudioGraphNodeState::STOPPED` | `PlaybackStateChanged{STOPPED}` | R→Core | |
| `…::PREPARING` | `PlaybackStateChanged{PREPARING}` | R→Core | Core maps to `BUFFERING`, unchanged. |
| `…::STREAMING` | `PlaybackStateChanged{PLAYING}` + `AudioFormatChanged` when `streamInfo` changed | R→Core | |
| `…::PAUSED` | `PlaybackStateChanged{PAUSED}` | R→Core | |
| `…::FINISHED` | `PlaybackStateChanged{FINISHED, source_token}` | R→Core | **This is end-of-source.** Deliberately *not* a separate `SourceEnded` message — the Core's FINISHED branch already drives queue advance and a second message would risk double-advance. `source_token` presence replaces the `current_stream_id is None` inference. |
| `…::ERROR` | `PlaybackStateChanged{ERROR, error}` + `PlaybackError` | R→Core | `PlaybackError` carries `ErrorSource` so the Core's `HTTP_STREAM`-only retry logic works unchanged. |
| `…::SOURCE_CHANGED` | `SourceChanged` | R→Core | Not a `PlaybackState`. Matches the Core, which returns early without dispatching a state event. |
| `StreamState.position` | `position_ms` + `position_valid` | R→Core | Renderer advances it to send time. `StreamState.timestamp` (a local `steady_clock`) is **not** on the wire. |
| `StreamState.streamInfo` | `AudioFormat` | R→Core | `stream_kind` + `stream_size_units` carried verbatim so `get_duration_ms()` is reused as-is. |
| `configureVolume(mode, mixer)` | *not exposed* | — | Renderer-local policy from its own config. Reflected in `VolumeCapabilities.backend`. |
| `getVolume()` | `VolumeState` in `StateSnapshot` | R→Core | |
| `setVolume(percent)` | `Command.set_volume` | Core→R | |
| `volumeMonitor()` / `wait()` | `VolumeChanged{external=true}` | R→Core | Preserves the "hardware knob moved" semantic `local-alsa` relies on. |
| `listAlsaPcmDevices()` | `StateSnapshot.output_devices`, `OutputDevicesChanged` | R→Core | Filtering (today in `alsa_options.py`) moves into the renderer, so the Core sees a clean list. |
| `output.alsa.device` config | `OutputDevice.alsa.pcm_name`, `selected_device_id` | R→Core | Selection is renderer-local; `SelectOutputDevice` is the proposed extension. |
| — (new) | `Hello` / `Welcome` / `Goodbye` | both | No native analogue. |
| — (new) | `CommandResult` | R→Core | Acceptance only; state arrival is a separate later event. |
| — (new) | `OwnershipChanged` | R→Core | Arbitration. |

No existing callback semantic is altered. The two structural changes — splitting
`SOURCE_CHANGED` out of the state enum, and attaching `source_token` to
`FINISHED` — both *encode* distinctions the Core currently reconstructs by hand.

### 5.1 Snapshot vs event vs ack vs observation

- **Full snapshot** — `StateSnapshot`, sent inside `Hello`, on reconnect, and in
  response to `RequestSnapshot`. One definition, no drift.
- **Discrete events** — `PlaybackStateChanged`, `SourceChanged`,
  `AudioFormatChanged`, `VolumeChanged`, `SelectedDeviceChanged`,
  `OutputDevicesChanged`, `PlaybackError`, `CapabilitiesChanged`,
  `OwnershipChanged`. End-of-source is `PlaybackStateChanged{FINISHED}`.
- **Acknowledgement** — `CommandResult{ACCEPTED}` means *queued to the player*,
  nothing more.
- **Observation** — the subsequent event that shows the requested state was
  actually reached. These are always two separate messages.

No high-frequency position reporting is added. The existing player emits state
changes only when they occur, and position rides along on each one.

---

## 6. Discovery, handshake, reconnection, shutdown

### 6.1 Discovery

`AvahiDiscovery` browses `_kalinkaplayer._tcp` continuously. On resolve it
produces `CoreEndpoint{server_id, host, addresses[], port, txt}`.

Deduplication is keyed on the **`server_id` TXT field**, which does not exist
yet and must be added to Core (§8). Until it does, the fallback key is
`(instance_name, port)`, which is unreliable because `service_name` is a
user-editable display string. Multiple A records / interfaces for one instance
collapse into one `CoreEndpoint` with an ordered address list; connection
attempts walk that list.

`StaticDiscovery` reads `core.endpoints` from the renderer config and emits the
same events — used on hosts without avahi-daemon, and by every test.

### 6.2 Per-Core connection lifecycle

One independent state machine per `server_id`:

```text
        CoreDiscovered
              │
              ▼
        [Connecting] ──── TCP/WS fail ────▶ [Backoff] ──▶ [Connecting]
              │                                 ▲
        WS established                          │
              ▼                                 │
        [Handshaking] ── send Hello(full state) │
              │                                 │
        Welcome received ◀── Goodbye/timeout ───┘
              ▼
        [Ready] ── commands in, events out
              │
     ┌────────┴────────┐
   close/EOF      CoreLost (mDNS)
     │                 │
     ▼                 ▼
  [Backoff]        [Teardown]  (reappearance re-enters Connecting)
```

1. Resolve address + endpoint. WS URL is `ws://<addr>:<port>/renderer/ws`.
2. Dedupe by `server_id`; one connection per Core regardless of how many records
   or interfaces surfaced it.
3. Connect, then send `Hello` **immediately** — before anything else — carrying
   `protocol_versions`, identity, capabilities and a complete `StateSnapshot`.
   There is no separate registration request: `Hello` *is* registration.
4. Await `Welcome` with a 10 s timeout. `Welcome` fixes the negotiated
   `protocol_version` and yields `server_id`; if that disagrees with the TXT
   `server_id`, the dedupe entry is re-keyed to the authoritative value.
5. `Goodbye{VERSION_UNSUPPORTED}` marks the Core incompatible, logs once, and
   backs off long (5 min) rather than hot-looping.
6. **Reconnect backoff**: 1 s → 2 s → 4 s → 8 s → 16 s → 30 s cap, ±20 % jitter,
   reset to 1 s after 60 s of a healthy connection. A fresh mDNS resolve for that
   `server_id` resets the backoff immediately.
7. **After reconnect the renderer sends a fresh `Hello` with the actual current
   state**, not a cached one. If audio kept playing across the drop, the new
   `Hello` says `PLAYING` with the live source token and the Core reconciles.
8. Service removal (`CoreLost`) tears the session down gracefully
   (`Goodbye{SHUTDOWN}`, close). Reappearance re-enters `Connecting`. If the lost
   Core held the ownership lease, the lease enters a 30 s grace period before
   release so a Wi-Fi blip does not hand control away mid-track.

### 6.3 Shutdown ordering

1. Signal handler sets a flag and posts to the io_context — it does no work.
2. Stop discovery (`avahi_threaded_poll_stop`), join its thread.
3. Send `Goodbye{SHUTDOWN}` on every session, close, `io_context.stop()`, join
   the network thread.
4. `StateMonitor::stop()` and `VolumeMonitor::stop()` unblock the two pump
   threads; join them.
5. Signal the command queue closed; the control thread drains and exits; join it.
6. `PlayerAgent` destroys the `AudioPlayer`, whose destructor calls `stop()` and
   closes ALSA.

Ordering invariant: **nothing that can post to a session outlives the network
thread, and nothing that can call into the player outlives the control thread.**

### 6.4 Threading and ownership model

| Thread | Owns | Never does |
|---|---|---|
| Discovery (Avahi threaded poll) | browse/resolve state | touch the player or a session directly; it posts `CoreDiscovered`/`CoreLost` to the network thread |
| Network (one `asio::io_context`) | all `Session` objects, all protobuf parse/serialize, reconnect timers | call into `AudioPlayer`; it only pushes onto the bounded command queue |
| Control (`PlayerAgent`) | the `AudioPlayer` instance | block on network I/O |
| StateMonitor pump | blocking `waitState()` loop | anything but translate + `asio::post` |
| VolumeMonitor pump | blocking `wait()` loop | anything but translate + `asio::post` |
| ALSA playback (`AlsaAudioEmitter::workerThread`) | real-time audio | *unchanged; no new code runs here* |

- Commands cross into the player through a **bounded MPSC queue** (capacity 64).
  On overflow the command is rejected with `CommandResult{REJECTED, QUEUE_FULL}`
  rather than dropped silently or blocking the network thread.
- Events cross back via `asio::post` onto the network strand — non-blocking by
  construction.
- Sessions are `shared_ptr` with `enable_shared_from_this`; every async handler
  captures `shared_from_this()`, so a completion handler cannot outlive its
  session. The `PlayerAgent` holds only `weak_ptr<Session>`, and fan-out is
  `asio::post(strand, [w]{ if (auto s = w.lock()) s->send(env); })`.

---

## 7. Command arbitration across multiple Cores

The renderer registers with **every** discovered Core, so two Cores can command
one physical output. This is not theoretical: the renderer has one ALSA device,
and two Cores each running their own queue would interleave `set_source` calls
and produce alternating tracks. A policy is mandatory.

| Policy | Description | Assessment |
|---|---|---|
| **A — none** | Last writer wins. | Simplest. Unusable: audible track ping-pong with no diagnosis path. |
| **B — ownership lease** (recommended) | All Cores connect and receive the full event stream. The first Core whose command would start or change playback acquires the lease; others are rejected with `NOT_OWNER` + owner identity. | Registers everywhere, but only one Core drives audio. Rejection carries enough information for a good UI message ("in use by *Living Room Kalinka*"). |
| **C — pinned primary** | Renderer config names one `server_id`; all others are permanent observers. | Deterministic, but defeats the point of registering everywhere and needs per-renderer manual configuration. |
| **D — Core-side negotiation** | Cores negotiate among themselves. | Rejected: no inter-Core channel exists; building one is a far larger project. |

**Policy B details.** The lease releases on `Stop`, on `ReleaseControl`, on
end-of-queue plus an idle timeout (default 60 s), or on owner disconnect plus a
30 s grace. `Command.force_takeover = true` overrides, so the app can prompt
"Take over playback?". `OwnershipChanged` is broadcast to all sessions.
Read-only commands (`RequestSnapshot`, `RefreshOutputDevices`) are never gated.
`SetVolume` **is** gated, on the grounds that volume on a shared output is as
disruptive as a track change.

**Recommendation: B, with C available as a config override**
(`arbitration.mode = lease | pinned`, `arbitration.pinned_server_id = …`).
This needs approval before implementation.

**Trust.** Zeroconf establishes discovery, not trust. There is no authentication
in the repo today and none is proposed here — the model stays trusted-LAN,
consistent with the existing unauthenticated REST API. Worth stating plainly in
the renderer README: this extends the unauthenticated surface from "read/control
a server" to "drive audio hardware".

**Heartbeat.** Deliberately omitted. WebSocket control frames already provide
liveness, uvicorn's `websockets` sends server→client pings, and half-open TCP is
detected by that mechanism. A browser renderer additionally *cannot* initiate
ping frames from JS, so an application-level heartbeat would be asymmetric for
no gain. Envelope fields 1013/2002 are reserved in case this is revisited.

### 7.1 Playback sessions (implemented)

Ahead of the lease policy above, the exclusivity primitive itself is in place: a
**playback session** is one Core's claim on the renderer's audio graph. The
Core mints the `session_id`; both sides hold it **in memory only**, so a crash
on either side ends the session and nothing false survives on disk.

Four identities, four lifetimes:

| ID | Minted by | Lifetime |
|---|---|---|
| `renderer_id` | renderer | persistent (`…/var/lib/kalinka-renderer/renderer_id`) |
| `instance_id` | renderer | per process |
| `server_id` | Core | persistent (`…/var/lib/kalinka/server_id`) |
| `session_id` | Core | per session, RAM-only on both sides |

`server_id` must persist because the renderer connects to *every* capable Core.
It reports its active session to all of them, tagged with the owning
`server_id`, and a Core acts only on sessions it owns — so a Core that crashed
and came back on a different port still recognises its own orphan, and other
Cores leave it alone.

**Reconciliation** runs on every `Hello` (`SessionPool.reconcile`):

| Renderer reports | Core holds | Outcome |
|---|---|---|
| our session `S` | `S` | resume — session rebinds to the new socket, playback never stops |
| our session `S` | nothing | `SessionClose{STALE}`; the renderer drops the orphan |
| our session `S` | `S`, still opening | abandoned; `open()` fails and the renderer is told to drop `S` |
| another Core's session | — | ignored |
| nothing | session `T` | `T` closed locally, reason `RENDERER_RESTARTED` |

A dropped link only **suspends** a session; the renderer keeps playing and the
Core waits for it to return. The registry's offline reap (60 s) closes the
session with `RENDERER_LOST`.

**The renderer releases sessions on its own**, because reconciliation alone is
not enough: it only fires when the *owning* `server_id` reconnects, so a Core
that was reinstalled — or whose `server_id` file was lost, or that runs with an
unwritable state directory and mints an ephemeral id each start — would leave
the renderer claimed by an owner that can never return, refusing every future
session. `SessionManager` therefore watches its owner's connections and, when
the last one drops:

- releases the session immediately if nothing is playing;
- otherwise releases it after a grace period (`--session-grace`, default 60 s)
  unless the owner reconnects first.

Playback is not implemented yet, so the state is hard-wired to `Stopped` and
only the first rule can currently fire; `SessionManager::setPlaybackState` is
the hook the player will drive.

Only the owning Core may close a session (`SessionClose` carries no authority
from anyone else), which keeps this release path from becoming a way for one
Core to end another's playback.

**Reopening after a close is always the owner's decision, never the pool's** —
`SessionPool` exposes `open()`, `get()` and per-session `close()` /
`on_closed()`, and applies no policy of its own. On Core shutdown sessions are
finalized and their callbacks fire, but uvicorn has already closed the renderer
sockets by then, so the renderer itself learns via `STALE` at its next `Hello`.

---

## 8. Build targets and dependency choices

**Build system: CMake for `packages/kalinka-renderer/`**, wrapped by the root
`Makefile` so the top-level UX is unchanged. Justification: protoc codegen with
correct dependency tracking, four linked targets, CTest integration and
multi-platform intent are all things the existing hand-written Makefiles would
do badly.

Root `Makefile` gains:

```make
renderer-build        # cmake -S packages/kalinka-renderer -B build/renderer && cmake --build
renderer-test         # ctest --test-dir build/renderer
proto                 # regenerate C++ (build-time) and Python (committed) bindings
proto-check           # regenerate Python bindings; fail if the tree changed  [CI guard]
kalinka-renderer-deb  # packaging/build_deb.sh
```

| Target | Kind | Contents | Links |
|---|---|---|---|
| `kalinka_audiograph` | STATIC (new, in the *server* package) | existing `native_player/*.cpp` minus `PyBindings.cpp`, **no** `-D__PYTHON__` | asound, FLAC++, FLAC, curlpp, curl, spdlog, fmt, pthread |
| `kalinka_renderer_proto` | STATIC | protoc output from `renderer.proto` | `protobuf-lite` |
| `kalinka_renderer_core` | STATIC | discovery, net, session, player, platform | above two + Boost headers, Avahi |
| `kalinka-renderer` | EXECUTABLE | `main.cpp` | `kalinka_renderer_core` |
| `kalinka_renderer_tests` | EXECUTABLE (CTest) | `tests/*.cpp` + fakes | `kalinka_renderer_core`, gtest, gmock |
| `renderer_proto_python` | custom | `protoc --python_out` into `kalinka_server/renderer_proto/` | — |

### 8.1 Dependencies

| Need | Choice | Why |
|---|---|---|
| WebSocket client | **Boost.Beast + Boost.Asio** (header-only) | Boost is *already* a declared build dep (`libboost-dev`, used by `Config.h`). Zero new packages on Debian/Ubuntu. Gives an executor for the network thread and a TLS path later. |
| Protobuf | **`libprotobuf-lite-dev` + `protobuf-compiler`** | Lite runtime is sufficient — no reflection, descriptors, text-format or JSON mapping is used. `optimize_for = LITE_RUNTIME` cuts binary size, which matters on a Pi. Lite and full runtimes are wire-compatible, so the Python side using the standard full runtime is fine. |
| mDNS browse | **`libavahi-client-dev`** behind `IDiscovery` | avahi-daemon is standard on Raspberry Pi OS / Debian. The interface lets Bonjour (macOS) and `DnssdQuery` (Windows) backends slot in later without touching session code. `StaticDiscovery` is the no-daemon fallback. |
| Tests | gtest / gmock | Matches `native_player/tests/Makefile`. Keep `-fsanitize=address` on for the test build, as today. |
| gRPC | **not used** | No existing dependency justifies it (§1.9). |

### 8.2 Generated sources: commit or generate?

- **C++: generate at build time.** The C++ build already requires a full
  toolchain; adding `protobuf-compiler` to the apt list in `release.yml` is one
  line, and CMake tracks the `.proto` dependency correctly.
- **Python: commit the generated `_pb2.py`.** Python wheels build inside minimal
  release containers and inside pip's isolated PEP-517 environment, where
  invoking `protoc` is fragile and would add a build-time dependency to a package
  that otherwise needs none. The generated file is small, pure Python and stable.
  `make proto-check` in CI regenerates and fails on any diff, so drift cannot
  land silently.

The asymmetry is deliberate; see open question 3.

### 8.3 Distribution

A new `kalinka-renderer` deb (arch- and platform-suffixed, same convention as
`kalinka-server`), a `kalinka-renderer.service` systemd unit, config at
`<prefix>/etc/kalinka/renderer.conf`, identity at
`<prefix>/var/lib/kalinka-renderer/identity`. Runtime deps: `libasound2`,
`libflac++`, `libcurlpp0`, `libspdlog`, `libfmt`, `libprotobuf-lite`,
`libavahi-client3`, plus `avahi-daemon` as a `Recommends`. It installs
**independently of kalinka-server** — that is the point of the split.

---

## 9. Python Core integration points

All additive; the existing local-playback path stays the default.

| # | File | Change | Phase |
|---|---|---|---|
| 1 | `kalinka_server/server_identity.py` **(new)** | Persisted UUIDv4 at `paths.state_dir()/server_id`, atomic write. | 1 |
| 2 | `service_discovery.py:55-58` | Add `server_id`, `renderer_ws=/renderer/ws`, `renderer_proto=1` to the TXT `desc` dict. Unknown TXT keys are ignored by existing clients, so this is backward compatible. | 1 |
| 3 | `kalinka_server/renderer_proto/` **(new)** | Committed generated `*_pb2.py` (+ `.pyi`). | 1 |
| 4 | `kalinka_server/renderer_ws_handler.py` **(new)** | Mirrors `queue_ws_handler.py` but binary: `receive_bytes()`/`send_bytes()`, `Envelope.ParseFromString`, dispatch on the `oneof`. Same two-task send/receive structure and cleanup. | 1 |
| 5 | `server.py:1321` | `@app.websocket("/renderer/ws")` next to the existing two endpoints. | 1 |
| 6 | `kalinka_server/renderer_registry.py` **(new)** | `renderer_id → session`, last snapshot, ownership state. `GET /renderer/list` for the app. | 1 |
| 7 | `config_model.py` | `RendererConfig { enabled, preferred_renderer_id }` on `KalinkaConfig`. | 3 |
| 8 | `playqueue.py:229` | Extract a `PlayerBackend` protocol (`append`/`remove`/`clear_all`/`stop`/`pause`/`resume`/`get_state`/`monitor`/`configure_volume`/`get_volume`/`set_volume`/`volume_monitor`) — exactly today's `AudioPlayer` surface. `NativePlayerBackend` wraps the extension (default, behaviour-identical); `RemoteRendererBackend` speaks the protocol. `AsyncStateMonitor` and `create_volume_control_device` keep working untouched because the interface is the one they already consume. | 3 |
| 9 | `kalinka_server/media_proxy.py` **(new, conditional)** | `GET /media/{token}` streaming endpoint with short-lived tokens, so `file://` sources can reach an off-box renderer. See open question 1. | 3 |

Phase 1 requires none of the `playqueue.py` surgery: the renderer connects,
registers and appears in `GET /renderer/list` while local playback continues
exactly as today.

---

## 10. Testing strategy

### 10.1 C++ unit tests

gtest/gmock + CTest, ASAN on as in the existing test Makefile. The two seams
that make this cheap are `IPlayer` and `ITransport`.

- `ProtocolSession_test` — handshake, version negotiation, malformed `Hello`,
  `Goodbye` paths, command→`CommandResult` correlation, snapshot-on-request.
  Uses `FakeTransport` + `FakePlayer`: **no sockets, no ALSA.**
- `StateTranslator_test` — every `AudioGraphNodeState` and `StreamError`
  combination → expected messages. This is where the "don't change existing
  semantics" contract is pinned down, including the `FINISHED`-with/without-
  `source_token` distinction.
- `CommandQueue_test` — bounded behaviour, reject-on-full, close-and-drain,
  multi-producer.
- `Arbiter_test` — lease acquire/reject/release, grace period on disconnect,
  forced takeover.
- `Backoff_test` — sequence, jitter bounds, reset conditions.
- `Identity_test` — first-boot creation, reuse, corrupt-file recovery.
- `OutputDevices_test` — ALSA hint filtering, fed synthetic hints (the same
  pure-function approach `alsa_options.py` already uses).

### 10.2 C++/Python Protobuf compatibility

`tests/interop/`, run in CI on both sides:

1. `emit_golden.cpp` serializes a fixed corpus of `Envelope`s — every message
   type, including absent optionals, empty repeated fields, max-value `uint64`,
   non-ASCII `friendly_name`, and all enum values — into
   `tests/interop/golden/*.bin`.
2. `test_interop.py` parses each with the committed Python bindings and asserts
   every field against an expected table.
3. The reverse: pytest serializes a Python-built corpus; a C++ test parses and
   asserts it.
4. `make proto-check` regenerates the Python bindings and fails if the working
   tree differs — the schema-drift guard.

This catches exactly the failures that matter: a field number changed on one
side, an enum value renumbered, or the Python bindings left stale after a
`.proto` edit.

### 10.3 Integration

A marked, opt-in pytest launches the built `kalinka-renderer` against a real
FastAPI test app with `StaticDiscovery` pointed at it and the ALSA device set to
`null`, asserting `Hello` → `Welcome` → snapshot → a `set_source`/`stop` round
trip. Plus `tests/test_renderer_ws_handler.py` on the Python side with a fake
WebSocket, following the pattern in `tests/test_alsa_volume_device.py`.

### 10.4 CI

Add a `renderer` job to `.github/workflows/release.yml` reusing the existing
arm64/amd64 container matrix, running `make renderer-build renderer-test
proto-check`.

---

## 11. Web-renderer reuse assessment

**Reusable as-is:**

- The entire `.proto` — no native-only field is mandatory.
- Binary WebSocket envelope framing; browsers handle `ArrayBuffer` WS messages
  natively.
- Core-side registration, session handling, registry and ownership arbitration —
  a web renderer is just another `renderer_id` with `RendererKind.WEB`.
- Capability and state semantics.
- **Generated Dart bindings** via `protoc_plugin`, since `kalinka-web` is the
  Flutter app's web build from the `madenvel/KalinkaAI` sibling repo. Not
  TypeScript.

**Native-only, correctly excluded from the protocol:**

- C++ glue, `PlayerAgent`, `AudioPlayer` integration.
- Avahi/Bonjour browsing — discovery is a renderer-local concern that never
  appears on the wire, which is exactly why a browser can skip it.
- ALSA/WASAPI/Core Audio enumeration.

**Browser constraints and how the schema absorbs them:**

| Constraint | Schema accommodation |
|---|---|
| No mDNS in a browser | Discovery is outside the protocol. The page learns the Core from its own origin (`kalinka-web` is served *by* the Core at `/`), so `ws://<origin>/renderer/ws` needs no configuration at all. |
| Output selection is permission-gated (`selectAudioOutput`, `setSinkId`) | `Capabilities.device_selection = false` unless granted; `OutputDevice.backend = WebAudioDevice{sink_id}`. `OutputDevice.id` is already opaque, which matters because browser `deviceId`s are origin-scoped and reset when permission lapses. |
| Volume is app-level gain, not a mixer | `VolumeState.backend = SOFTWARE`, `reports_external_changes = false`. No new fields needed. |
| Decoded format may be unavailable | `AudioFormat` is `optional` everywhere; `position_valid` covers unreliable position. |
| Autoplay policy blocks unprompted playback | `CommandResult{REJECTED, NOT_READY}` already expresses "needs a user gesture". |
| JS cannot initiate WS ping frames | Non-issue: liveness rides on server→client pings, which uvicorn's `websockets` sends and browsers answer automatically. A concrete reason **not** to add an application-level heartbeat. |
| No `file://` access | `Capabilities.supported_uri_schemes = ["http","https"]`. The Core already has to consult this field for the native case. |

**Conclusion:** the schema supports both cleanly with no weakening of the native
design, because every backend-specific field is confined to the `oneof backend`
or gated by an explicit capability. The web renderer stays a separate later
implementation and build target, in the app repo.

---

## 12. Open questions, risks, and decisions requiring approval

### Decisions needed before implementation

1. **`file://` sources are blocking for an off-box renderer.**
   `kalinka_plugin_localfiles/localfiles.py:618` returns `file://{track_path}` —
   a path on the *Core's* filesystem. Qobuz/Jamendo return HTTPS and work
   anywhere; **local files, the primary use case, do not.** Options: (a) add a
   `GET /media/{token}` streaming endpoint to Core and rewrite `file://` when the
   target renderer is remote; (b) restrict Phases 1–2 to a co-located renderer
   (same box, same filesystem) and defer; (c) require a shared mount.
   *Recommendation: **(b) then (a)*** — co-located first proves the whole stack
   with zero Core media work, and the media proxy lands in Phase 3 when the Core
   actually drives a remote renderer.

2. **Arbitration policy** — recommend the ownership lease (Policy B, §7) with a
   `pinned` config override.

3. **Committed Python `_pb2.py` vs build-time generation** — recommend
   committed, with a `proto-check` CI guard (§8.2).

4. **`SOURCES.txt` in the server package**, read by both `setup.py` and the new
   `native_player/CMakeLists.txt`. A small edit to an existing, working build
   file; the alternative is two hand-maintained source lists that will drift.

5. **Adding `server_id` to the mDNS TXT record.** Backward compatible (unknown
   TXT keys are ignored), but it is a change to the discovery contract the
   Flutter app also consumes.

6. **Seek.** The brief says no seek is required, but Core already exposes
   `PUT /queue/current_track/seek` and `AudioPlayer::seek()` works. Dropping it
   for remote renderers would be a visible regression. `Seek` is defined in the
   schema and capability-gated off — confirm that "define now, implement later"
   is the intent.

### Risks

7. **avahi-daemon vs python-zeroconf on the same host.** Core uses
   `python-zeroconf`; the renderer would use avahi-client, which needs
   avahi-daemon. Both want UDP 5353. They generally coexist via `SO_REUSEPORT`,
   but this is a known friction point and the combined server+renderer box is the
   common case. Mitigation: `StaticDiscovery` (or a `localhost` shortcut) when
   co-located, so the default combined install needs no mDNS at all.

8. **Position/clock semantics.** `playqueue.py:396-400` computes `position_diff`
   from `time.monotonic_ns() - StreamState.timestamp`, valid only because both
   are in one process. That arithmetic **must not** run for a remote renderer.
   `RemoteRendererBackend` passes through the already-advanced `position_ms` and
   skips the correction.

9. **Volume ownership split.** The Core's `local-alsa` device drives volume on
   the Core's own `AudioPlayer`. With a remote renderer, volume must route to the
   renderer's `SetVolume` instead. Handled by the `PlayerBackend` split
   (integration point 8) — but it means `local-alsa` and a remote renderer are
   mutually exclusive, which the settings UI should express.

10. **Boost.Beast with `-std=c++23`** on Debian trixie (Boost 1.83) and Ubuntu
    24.04. Expected fine; verify early in Phase 1 rather than discovering it in
    Phase 4. Fallback: compile the network layer at C++20 as a separate
    translation unit.

11. **Two prefetched streams max.** The Core prefetches one track 5 s ahead
    (`PREFETCH_TIME_MS`). `max_queued_sources` will advertise 2; the Core must
    respect it or gapless behaviour will differ from local playback.

---

## 13. Phased implementation plan (post-approval)

**Phase 0 — decisions.** Resolve items 1–6 above. No code.

**Phase 1 — boilerplate, protocol, handshake.**
Package skeleton, CMake, root-Makefile targets. `renderer.proto` + codegen on
both sides. `Identity`, `RendererConfig`, `StaticDiscovery`, `AvahiDiscovery`,
`BeastWebSocket`, `ConnectionManager`, `ProtocolSession` — driven by
`FakePlayer`, so no audio yet. Core side: `server_identity.py`, TXT `server_id`,
`/renderer/ws`, `renderer_registry.py`, `GET /renderer/list`. Tests: all unit
tests plus the interop golden vectors.
*Done when:* a renderer binary with a fake player discovers a Core, completes
`Hello`/`Welcome`, and shows up in `GET /renderer/list`. Local playback
untouched.

**Phase 2 — real playback.**
`kalinka_audiograph` static lib + `SOURCES.txt`. `IPlayer`/`NativePlayer`,
`PlayerAgent`, `CommandQueue`, the two monitor pumps, `StateTranslator`,
`SourceTable`, `OutputDevices`. A CLI harness (`kalinka-renderer --play <uri>`)
for manual verification.
*Done when:* a Core can drive `set_source` / `enqueue_source` / `pause` / `stop`
/ `set_volume` against real hardware, gapless works, and every event maps per the
table in §5.

**Phase 3 — Core drives the renderer.**
`PlayerBackend` extraction in `playqueue.py`, `NativePlayerBackend`
(behaviour-identical default), `RemoteRendererBackend`, renderer selection
config, volume routing. Media proxy for `file://` if approved. This is where the
playqueue tests become relevant and get run.

**Phase 4 — arbitration and resilience.**
`Arbiter`, reconnect backoff hardening, multi-Core soak test, network-partition
and Core-restart scenarios.

**Phase 5 — packaging.**
`kalinka-renderer` deb, systemd unit, CI matrix job, `install-release.sh`
integration, README.

**Phase 6 — later, separately scoped.**
`SelectOutputDevice` implementation (rebuild `AudioPlayer` on the control
thread). Web renderer in the app repo with Dart bindings.
