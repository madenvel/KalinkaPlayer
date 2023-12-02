#include "AudioPlayer.h"
#include <fstream>
#include <unistd.h>

int main() {
  AudioPlayer player;
  auto contextId =
      player.prepare("https://getsamplefiles.com/download/flac/sample-1.flac",
                     10 * 1024 * 1024, 64 * 1024);

  auto context2 = player.prepare(
      "https://streaming-qobuz-std.akamaized.net/"
      "file?uid=1040320&eid=26457483&fmt=7&profile=raw&app_id=950096963&cid="
      "1178610&etsp=1701551401&hmac=cK3P9XEsQPqj1CrAbKqvzbtjnPQ");

  player.setStateCallback([](int context, State, State newState) {
    std::cout << "State changed to " << newState << ", context = " << context
              << std::endl;
  });
  player.setProgressUpdateCallback([](float progress) {
    std::cout << "Progress: " << progress << std::endl;
  });

  player.play(contextId);
  sleep(5);
  player.play(context2);
  sleep(5);
  player.stop();

  return 0;
}
