#include "AudioPlayer.h"
#include <fstream>
#include <unistd.h>

int main() {
  AudioPlayer player;
  auto contextId =
      player.prepare("https://getsamplefiles.com/download/flac/sample-1.flac",
                     10 * 1024 * 1024, 64 * 1024);

  player.setStateCallback([](int, State, State newState) {
    std::cout << "State changed to " << newState << std::endl;
  });
  player.setProgressUpdateCallback([](float progress) {
    std::cout << "Progress: " << progress << std::endl;
  });

  player.play(contextId);
  sleep(3);
  player.stop();

  return 0;
}
