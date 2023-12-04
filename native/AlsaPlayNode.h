#ifndef ALSA_PLAYNODE_H
#define ALSA_PLAYNODE_H

#include <atomic>
#include <functional>
#include <memory>
#include <stdexcept>
#include <thread>

#include "ProcessNode.h"

class StateMachine;

using ContextProgressUpdateCallback = std::function<void(float)>;

class AlsaDevice {
public:
  AlsaDevice();
  ~AlsaDevice();

  void *getHandle(int sampleRate, int bitsPerSample);
  int getCurrentSampleRate() const { return currentSampleRate; }

  int getCurrentBitsPerSample() const { return currentBitsPerSample; }

private:
  void *pcmHandle = nullptr;
  int currentSampleRate = 0;
  int currentBitsPerSample = 0;

  void init(int sampleRate, int bitsPerSample);
  void openDevice();
};

class AlsaPlayNode : public ProcessNode {

private:
  std::weak_ptr<AlsaDevice> alsaDevice;
  int sampleRate;
  int bitsPerSample;
  std::shared_ptr<StateMachine> sm;
  ContextProgressUpdateCallback progressCb;
  size_t totalFrames;
  std::jthread playThread;
  bool paused = false;

  std::weak_ptr<ProcessNode> in;

public:
  AlsaPlayNode(std::shared_ptr<AlsaDevice> alsaDevice, int sampleRate,
               int bitsPerSample, size_t totalFrames,
               std::shared_ptr<StateMachine> sm,
               ContextProgressUpdateCallback progressCb)
      : alsaDevice(alsaDevice), sampleRate(sampleRate),
        bitsPerSample(bitsPerSample), sm(sm), progressCb(progressCb),
        totalFrames(totalFrames) {}

  ~AlsaPlayNode() = default;

  void pause(bool paused_);

  virtual void connectIn(std::shared_ptr<ProcessNode> inputNode) override;

  virtual void start() override;
  virtual void stop() override;

private:
  int workerThread(std::stop_token token);
};

#endif