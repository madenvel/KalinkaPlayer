#ifndef ALSA_AUDIO_EMITTER_H
#define ALSA_AUDIO_EMITTER_H

#include "AudioGraphNode.h"

#include <alsa/asoundlib.h>

#include <functional>
#include <list>
#include <thread>
#include <vector>

class PlayedFramesCounter {
public:
  PlayedFramesCounter() = default;
  void update(snd_pcm_sframes_t frames);

  snd_pcm_sframes_t getPlayedFrames() const { return lastPlayedFrames; }

  std::vector<snd_pcm_sframes_t> drainSequence();

  void
  callOnOrAfterFrame(snd_pcm_sframes_t frame,
                     std::function<void(snd_pcm_sframes_t)> onFramesPlayed);

  void reset();

private:
  snd_pcm_sframes_t lastPlayedFrames = 0;

  std::list<
      std::pair<snd_pcm_sframes_t, std::function<void(snd_pcm_sframes_t)>>>
      onFramesPlayedCallbacks;
};

class AlsaAudioEmitter : public AudioGraphEmitterNode {
public:
  AlsaAudioEmitter(const std::string &deviceName, size_t bufferSize = 16384,
                   size_t periodSize = 1024);

  virtual void
  connectTo(std::shared_ptr<AudioGraphOutputNode> outputNode) override;
  virtual void
  disconnect(std::shared_ptr<AudioGraphOutputNode> outputNode) override;

  virtual void pause(bool paused) override;

  virtual ~AlsaAudioEmitter();

private:
  std::string deviceName;
  std::shared_ptr<AudioGraphOutputNode> inputNode;
  std::jthread playbackThread;

  StreamAudioFormat currentStreamAudioFormat;
  PlayedFramesCounter playedFramesCounter;
  snd_pcm_t *pcmHandle = nullptr;

  snd_pcm_uframes_t bufferSize;
  snd_pcm_uframes_t periodSize;
  std::vector<pollfd> ufds;

  std::atomic<snd_pcm_sframes_t> currentSourceTotalFramesWritten = 0;
  std::atomic<bool> paused = false;

  void workerThread(std::stop_token token);
  void setupAudioFormat(const StreamAudioFormat &streamAudioFormat);
  StreamState waitForInputToBeReady(std::stop_token token);

  bool openDevice();
  void closeDevice();

  snd_pcm_sframes_t waitForAlsaBufferSpace(std::stop_token stopToken);
  bool hasInputSourceStateChanged();
  snd_pcm_sframes_t writeToAlsa(
      snd_pcm_uframes_t framesToWrite,
      std::function<snd_pcm_sframes_t(void *ptr, snd_pcm_uframes_t frames,
                                      size_t bytes)>
          func);
  snd_pcm_sframes_t readIntoAlsaFromStream(std::stop_token stopToken,
                                           snd_pcm_sframes_t framesToRead);
  size_t waitForInputData(std::stop_token stopToken, snd_pcm_uframes_t frames);
  inline std::chrono::milliseconds framesToTimeMs(snd_pcm_sframes_t frames);

  void startPcmStream(const StreamInfo &streamInfo, snd_pcm_uframes_t position);
  void drainPcm();

  void start();
  void stop();
};

#endif