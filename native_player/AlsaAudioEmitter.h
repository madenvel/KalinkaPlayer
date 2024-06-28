#ifndef ALSA_AUDIO_EMITTER_H
#define ALSA_AUDIO_EMITTER_H

#include "AudioGraphNode.h"

#include <alsa/asoundlib.h>

#include <functional>
#include <thread>
#include <vector>

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
  snd_pcm_t *pcmHandle = nullptr;

  snd_pcm_uframes_t bufferSize;
  snd_pcm_uframes_t periodSize;
  std::vector<pollfd> ufds;
  std::vector<uint8_t> buffer;

  std::function<size_t(size_t)> framesToBytes;
  std::function<size_t(size_t)> bytesToFrames;

  void workerThread(std::stop_token token);
  void setupAudioFormat(const StreamAudioFormat &streamAudioFormat);
  StreamState waitForInputToBeReady(std::stop_token token);

  void start();
  void stop();
};

#endif