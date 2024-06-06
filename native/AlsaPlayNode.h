#ifndef ALSA_PLAYNODE_H
#define ALSA_PLAYNODE_H

#include <atomic>
#include <functional>
#include <memory>
#include <stdexcept>
#include <thread>

#include "ProcessNode.h"

class StateMachine;

class AlsaDevice {
public:
  AlsaDevice();
  ~AlsaDevice();

  void *getHandle(unsigned int sampleRate, unsigned int bitsPerSample);
  unsigned int getCurrentSampleRate() const { return currentSampleRate; }

  unsigned int getCurrentBitsPerSample() const { return currentBitsPerSample; }

private:
  void *pcmHandle = nullptr;
  unsigned int currentSampleRate = 0;
  unsigned int currentBitsPerSample = 0;

  void init(unsigned int sampleRate, unsigned int bitsPerSample);
  void openDevice();
};

class AlsaPlayNode : public ProcessNode {

private:
  std::weak_ptr<AlsaDevice> alsaDevice;
  std::shared_ptr<StateMachine> sm;
  std::jthread playThread;
  bool paused = false;

  std::weak_ptr<ProcessNode> in;

public:
  AlsaPlayNode(std::shared_ptr<AlsaDevice> alsaDevice,
               std::shared_ptr<StateMachine> sm)
      : alsaDevice(alsaDevice), sm(sm) {}

  ~AlsaPlayNode() = default;

  void pause(bool paused_);

  virtual void connectIn(std::shared_ptr<ProcessNode> inputNode) override;

  virtual void start() override;
  virtual void stop() override;

private:
  int workerThread(std::stop_token token);
};

#endif