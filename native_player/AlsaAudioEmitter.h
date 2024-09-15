#ifndef ALSA_AUDIO_EMITTER_H
#define ALSA_AUDIO_EMITTER_H

#include "AudioGraphNode.h"
#include "Utils.h"

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
                   size_t periodSize = 1024, size_t sleepAfterFormatSetupMs = 0,
                   bool reopenDeviceWithNewFormat = false);

  virtual void
  connectTo(std::shared_ptr<AudioGraphOutputNode> outputNode) override;
  virtual void
  disconnect(std::shared_ptr<AudioGraphOutputNode> outputNode) override;

  virtual void pause(bool paused) override;
  virtual size_t seek(size_t positionMs) override;

  virtual ~AlsaAudioEmitter();

private:
  std::string deviceName;
  snd_pcm_uframes_t requestedBufferSize;
  snd_pcm_uframes_t requestedPeriodSize;
  snd_pcm_uframes_t bufferSize;
  snd_pcm_uframes_t periodSize;

  // Fixups
  size_t sleepAfterFormatSetupMs;
  bool reopenDeviceWithNewFormat;

  std::shared_ptr<AudioGraphOutputNode> inputNode;
  std::jthread playbackThread;
  std::atomic<bool> isWorkerRunning = false;

  StreamAudioFormat currentStreamAudioFormat;
  PlayedFramesCounter playedFramesCounter;
  snd_pcm_sframes_t currentSourceTotalFramesWritten = 0;
  std::chrono::milliseconds pollTimeout = std::chrono::milliseconds(100);
  snd_pcm_t *pcmHandle = nullptr;
  std::vector<pollfd> ufds;

  Signal<size_t> seekRequestSignal;
  Signal<bool> pauseRequestSignal;
  bool paused = false;
  bool seekHappened = false;

  void workerThread(std::stop_token token);
  bool handleSeekSignal();
  bool handlePauseSignal(bool paused);

  void openDevice();
  void closeDevice();

  void setupAudioFormat(const StreamAudioFormat &streamAudioFormat);
  StreamState waitForInputToBeReady(std::stop_token token);
  snd_pcm_sframes_t waitForAlsaBufferSpace();
  bool handleInputNodeStateChange();
  snd_pcm_sframes_t writeToAlsa(
      snd_pcm_uframes_t framesToWrite,
      std::function<snd_pcm_sframes_t(void *ptr, snd_pcm_uframes_t frames,
                                      size_t bytes)>
          func);
  snd_pcm_sframes_t readIntoAlsaFromStream(std::stop_token stopToken,
                                           snd_pcm_sframes_t framesToRead);
  size_t waitForInputData(std::stop_token stopToken, snd_pcm_uframes_t frames);
  void startPcmStream(const StreamInfo &streamInfo, snd_pcm_uframes_t position);
  void drainPcm();

  inline std::chrono::milliseconds framesToTimeMs(snd_pcm_sframes_t frames);

  void start();
  void stop();

#if 0
  void setState(const StreamState &newState);
#endif
};
;

#endif