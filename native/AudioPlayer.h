#ifndef AUDIO_PLAYER_H
#define AUDIO_PLAYER_H

#include "AlsaPlayNode.h"
#include "AudioInfo.h"
#include "BufferNode.h"
#include "DataSource.h"
#include "FlacDecoder.h"
#include "StateMachine.h"
#include "ThreadPool.h"

#include <map>
#include <memory>

/*
 * Callbacks:
 * 1. On State Change (incl. onError)
 * 2. On Progress (ticker)
 * 3. On Finished Playback
 */

using StateCallback = std::function<void(int, State, long)>;
using ContextStateCallback = std::function<void(State, long)>;
using ContextProgressUpdateCallback = std::function<void(float)>;

class AudioPlayer : private ThreadPool {
private:
  class Context {
    std::shared_ptr<HttpRequestNode> httpNode;
    std::shared_ptr<BufferNode> flacBuffer;
    std::shared_ptr<FlacDecoder> decoder;
    std::shared_ptr<BufferNode> decodedData;
    std::shared_ptr<AlsaPlayNode> alsaPlay;
    std::shared_ptr<AlsaDevice> alsaDevice;

    ContextStateCallback stateCb;
    std::shared_ptr<StateMachine> sm;
    size_t bufferSize;

  public:
    Context(std::string url, ContextStateCallback stateCb,
            std::shared_ptr<AlsaDevice> alsaDevice);
    ~Context() = default;

    void prepare(size_t level1Buffer, size_t level2Buffer,
                 std::stop_token token);
    void play();
    void pause(bool paused);
    AudioInfo getStreamInfo();
    std::string getLastError();
  };

  StateCallback stateCb = [](int, State, long) {};
  std::shared_ptr<AlsaDevice> alsaDevice = std::make_shared<AlsaDevice>();
  std::map<int, std::unique_ptr<Context>> contexts;
  ThreadPool cbThreadPool = ThreadPool(1);
  int currentContextId = -1;
  int newContextId = 0;
  std::pair<int, std::string> lastErrorForContext = {-1, ""};

public:
  AudioPlayer();
  ~AudioPlayer();

  int prepare(const char *url, size_t level1BufferSize,
              size_t level2BufferSize);
  void removeContext(int contextId);
  void play(int contextId);
  void stop();
  void pause(bool paused);
  void seek(int time);
  std::string getLastError(int contextId);
  void setStateCallback(StateCallback cb);
  AudioInfo getAudioInfo(int contextId);

private:
  void onStateChangeCb_internal(int contextId, State oldState, long position);
};

#endif