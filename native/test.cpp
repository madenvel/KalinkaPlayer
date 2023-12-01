#include "AudioPlayer.h"
#include <iostream>

using namespace std;

#include <chrono>
#include <thread>

int main() {
  AudioPlayer player;

  player.setStateCallback([](int contextId, State oldState, State newState) {
    cout << "Context " << contextId << " changed state from " << oldState
         << " to " << newState << endl;
  });

  auto contextId =
      player.prepare("https://filesamples.com/samples/audio/flac/sample3.flac",
                     5 * 1024 * 1024, 64 * 1024);

  player.play(contextId);

  // Sleep for 10 seconds
  std::this_thread::sleep_for(std::chrono::seconds(10));

  return 0;
}