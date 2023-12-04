#ifndef AUDIO_PLAYER_H
#define AUDIO_PLAYER_H

#include "AlsaPlayNode.h"
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

using StateCallback = std::function<void(int, State, State)>;
using ContextStateCallback = std::function<void(State, State)>;
using ContextProgressUpdateCallback = std::function<void(float)>;
using ProgressUpdateCallback = std::function<void(int, float)>;

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
    ContextProgressUpdateCallback progressCb;
    size_t bufferSize;

  public:
    Context(std::string url, ContextStateCallback stateCb,
            ContextProgressUpdateCallback progressCb,
            std::shared_ptr<AlsaDevice> alsaDevice);
    ~Context() = default;

    void prepare(size_t level1Buffer, size_t level2Buffer,
                 std::stop_token token);
    void play();
    void pause(bool paused);
  };

  StateCallback stateCb = [](int, State, State) {};
  ProgressUpdateCallback progressCb = [](int, float) {};
  std::shared_ptr<AlsaDevice> alsaDevice = std::make_shared<AlsaDevice>();
  std::map<int, std::unique_ptr<Context>> contexts;
  ThreadPool cbThreadPool = ThreadPool(1);
  int currentContextId = -1;
  int newContextId = 0;

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
  void setStateCallback(StateCallback cb);
  void setProgressUpdateCallback(ProgressUpdateCallback cb);

private:
  void onStateChangeCb_internal(int contextId, State oldState, State newState);
  void onProgressUpdateCb_internal(int contextId, float progress);
};

#endif