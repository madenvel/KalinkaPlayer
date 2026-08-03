cmake_minimum_required(VERSION 3.16)
project(kalinka-renderer VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Protobuf REQUIRED)
# CONFIG: use Boost's own package config (the FindBoost module is removed in
# new CMake, policy CMP0167). Header-only use: asio + beast.
find_package(Boost CONFIG REQUIRED)
find_package(spdlog REQUIRED)
find_package(Threads REQUIRED)

# mDNS/DNS-SD: vendored public-domain single header (third_party/mdns).

# --- kalinka_renderer_proto: generated protobuf-lite C++ ---------------------
set(PROTO_FILE ${CMAKE_CURRENT_SOURCE_DIR}/proto/kalinka/renderer/v1/renderer.proto)
set(PROTO_GEN_DIR ${CMAKE_CURRENT_BINARY_DIR}/proto_gen)
file(MAKE_DIRECTORY ${PROTO_GEN_DIR})

add_custom_command(
  OUTPUT ${PROTO_GEN_DIR}/kalinka/renderer/v1/renderer.pb.cc
         ${PROTO_GEN_DIR}/kalinka/renderer/v1/renderer.pb.h
  COMMAND ${Protobuf_PROTOC_EXECUTABLE}
          -I ${CMAKE_CURRENT_SOURCE_DIR}/proto
          --cpp_out=${PROTO_GEN_DIR}
          ${PROTO_FILE}
  DEPENDS ${PROTO_FILE}
  COMMENT "Generating C++ protobuf sources from renderer.proto"
)

add_library(kalinka_renderer_proto STATIC
  ${PROTO_GEN_DIR}/kalinka/renderer/v1/renderer.pb.cc
)
target_include_directories(kalinka_renderer_proto SYSTEM PUBLIC ${PROTO_GEN_DIR})
target_link_libraries(kalinka_renderer_proto PUBLIC protobuf::libprotobuf-lite)

# --- kalinka-renderer executable ---------------------------------------------
add_executable(kalinka-renderer
  src/main.cpp
  src/Daemon.cpp
  src/Identity.cpp
  src/discovery/MdnsDiscovery.cpp
  src/config/ConfigService.cpp
  src/net/ConnectionManager.cpp
  src/net/CoreConnection.cpp
  src/player/StubPlayer.cpp
  src/session/Session.cpp
  src/session/SessionManager.cpp
)
target_compile_definitions(kalinka-renderer PRIVATE
  KALINKA_RENDERER_VERSION="${PROJECT_VERSION}"
)
target_include_directories(kalinka-renderer SYSTEM PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/third_party/mdns
)
target_link_libraries(kalinka-renderer PRIVATE
  kalinka_renderer_proto
  Boost::headers
  spdlog::spdlog
  Threads::Threads
)
