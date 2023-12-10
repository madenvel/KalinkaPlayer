#include "AudioPlayer.h"
#include <chrono>
#include <iostream>
#include <thread>

using namespace std;

int main() {
  AudioPlayer player;
  player.setStateCallback([&player](int context, State, State newState) {
    std::cout << "State changed to " << newState << ", context = " << context
              << std::endl;

    if (newState == State::ERROR) {
      std::cout << "Last error: " << player.getLastError(context) << std::endl;
    }
  });
  player.setProgressUpdateCallback([](int context, float progress) {
    std::cout << "Progress: " << progress << std::endl;
  });
  auto contextId =
      player.prepare("https://getsamplefiles.com/download/flac/sample-1.flac",
                     10 * 1024 * 1024, 64 * 1024);

  auto context2 =
      player.prepare("https://getsamplefiles.com/download/flac/sample-2.flac",
                     10 * 1024 * 1024, 64 * 1024);

  player.play(contextId);
  std::this_thread::sleep_for(std::chrono::seconds(3));
  player.play(context2);
  std::this_thread::sleep_for(std::chrono::seconds(30));
  player.stop();

  return 0;
}