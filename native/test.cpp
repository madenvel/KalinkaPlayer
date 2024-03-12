#include "AudioPlayer.h"
#include <chrono>
#include <iostream>
#include <thread>

using namespace std;

int main() {
  AudioPlayer player;
  auto start = std::chrono::steady_clock::now();
  player.setStateCallback([&player, start](int context, State newState,
                                           long position) {
    auto eventTime = std::chrono::steady_clock::now();
    std::cout << "State changed to " << newState << ", context = " << context
              << ", position = " << position << ", time=" << (eventTime - start)
              << std::endl;

    if (newState == State::ERROR) {
      std::cout << "Last error: " << player.getLastError(context) << std::endl;
    }
  });
  auto context1 =
      player.prepare("https://getsamplefiles.com/download/flac/sample-1.flac",
                     10 * 1024 * 1024, 64 * 1024);

  player.play(context1);
  cout << "Playing from context 1" << endl;
  std::this_thread::sleep_for(std::chrono::seconds(3));
  cout << "Playing from context 2" << endl;
  auto context2 =
      player.prepare("https://getsamplefiles.com/download/flac/sample-2.flac",
                     10 * 1024 * 1024, 64 * 1024);
  player.play(context2);
  std::this_thread::sleep_for(std::chrono::seconds(3));
  cout << "Paused for context 2 at "
       << (std::chrono::steady_clock::now() - start) << endl;
  player.pause(true);
  std::this_thread::sleep_for(std::chrono::seconds(3));
  cout << "Playing from context 2, at "
       << (std::chrono::steady_clock::now() - start) << endl;
  player.pause(false);
  std::this_thread::sleep_for(std::chrono::seconds(3));
  cout << "Stopped at " << (std::chrono::steady_clock::now() - start) << endl;
  player.stop();
  std::this_thread::sleep_for(std::chrono::seconds(3));

  return 0;
}