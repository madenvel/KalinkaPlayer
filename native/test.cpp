#include "AudioPlayer.h"
#include <chrono>
#include <iostream>
#include <thread>

using namespace std;

int main() {
  AudioPlayer player;
  // auto contextId =
  //     player.prepare("https://getsamplefiles.com/download/flac/sample-1.flac",
  //                    10 * 1024 * 1024, 64 * 1024);

  auto context2 = player.prepare(
      "https://streaming-qobuz-std.akamaized.net/"
      "file?uid=1040320&eid=26457483&fmt=7&profile=raw&app_id=950096963&cid="
      "1178610&etsp=1701647909&hmac=mT_NqiuE-Oi3pkDU2Jpjcql6s20",
      10 * 1024 * 1024, 64 * 1024);

  player.setStateCallback([](int context, State, State newState) {
    std::cout << "State changed to " << newState << ", context = " << context
              << std::endl;
  });
  player.setProgressUpdateCallback([](float progress) {
    std::cout << "Progress: " << progress << std::endl;
  });

  // player.play(contextId);
  // std::this_thread::sleep_for(std::chrono::seconds(3));
  player.play(context2);
  std::this_thread::sleep_for(std::chrono::seconds(30));
  player.stop();

  return 0;
}