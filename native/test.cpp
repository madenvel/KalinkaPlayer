#include "AudioPlayer.h"
#include <chrono>
#include <iostream>
#include <thread>

using namespace std;

int main() {
  AudioPlayer player;
  auto start = std::chrono::steady_clock::now();
  player.setStateCallback([start](int context, const StateInfo state) {
    auto eventTime = std::chrono::steady_clock::now();
    std::cout << "State changed to " << state.state << ", context = " << context
              << ", position = " << state.position
              << ", time=" << (eventTime - start) << std::endl;

    if (state.state == State::ERROR) {
      std::cout << "Message: " << state.message << std::endl;
    }
  });
  auto context1 =
      player.prepare("https://getsamplefiles.com/download/flac/sample-1.flac",
                     10 * 1024 * 1024, 64 * 1024);

  auto context2 =
      player.prepare("https://getsamplefiles.com/download/flac/sample-2.flac",
                     10 * 1024 * 1024, 64 * 1024);

  std::this_thread::sleep_for(std::chrono::seconds(3));

  std::cerr << std::chrono::duration_cast<std::chrono::milliseconds>(
                   std::chrono::system_clock::now().time_since_epoch())
                   .count()
            << " Start playback 1 - client" << std::endl;

  player.play(context1);
  cout << "Playing from context 1" << endl;
  std::this_thread::sleep_for(std::chrono::seconds(3));
  cout << "Playing from context 2" << endl;
  std::cerr << std::chrono::duration_cast<std::chrono::milliseconds>(
                   std::chrono::system_clock::now().time_since_epoch())
                   .count()
            << " Start playback 2 - client" << std::endl;
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

  player.prepare("http://www.google.com", 10 * 1024 * 1024, 64 * 1024);

  std::this_thread::sleep_for(std::chrono::seconds(3));

  return 0;
}