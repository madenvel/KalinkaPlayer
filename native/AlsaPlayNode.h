#ifndef ALSA_PLAYNODE_H
#define ALSA_PLAYNODE_H

#include <atomic>
#include <functional>
#include <memory>
#include <stdexcept>
#include <thread>

#include "ProcessNode.h"

class StateMachine;

class AlsaPlayNode : public ProcessNode {
  using ProgressUpdateCallback = std::function<void(float)>;

private:
  int sampleRate;
  int bitsPerSample;
  std::shared_ptr<StateMachine> sm;
  ProgressUpdateCallback progressCb;
  size_t totalFrames;
  std::jthread playThread;
  bool paused = false;

  std::weak_ptr<ProcessNode> in;

public:
  AlsaPlayNode(int sampleRate, int bitsPerSample, size_t totalFrames,
               std::shared_ptr<StateMachine> sm,
               ProgressUpdateCallback progressCb)
      : sampleRate(sampleRate), bitsPerSample(bitsPerSample), sm(sm),
        progressCb(progressCb), totalFrames(totalFrames) {}

  ~AlsaPlayNode() = default;

  void pause(bool paused_);

  virtual void connectIn(std::shared_ptr<ProcessNode> inputNode) override;

  virtual void start() override;
  virtual void stop() override;

private:
  int workerThread(std::stop_token token);
};

#endif