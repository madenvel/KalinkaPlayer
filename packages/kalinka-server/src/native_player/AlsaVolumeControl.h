#ifndef ALSA_VOLUME_CONTROL_H
#define ALSA_VOLUME_CONTROL_H

#include <condition_variable>
#include <functional>
#include <list>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
#include <utility>

#include <alsa/asoundlib.h>

/// Which backend currently provides volume. Surfaced to Python so it knows
/// whether external-change monitoring is meaningful (hardware only).
enum class VolumeBackend { None = 0, Hardware = 1, Software = 2 };

/// User-selected policy, from `output.alsa.volume_mode`.
enum class VolumeMode { Auto, Hardware, Software, Fixed };

VolumeMode parseVolumeMode(const std::string &mode);

/// Percent-based snapshot of the active volume backend.
///
/// `current`/`max` are a 0..100 scale. `backend` is a `VolumeBackend` value so
/// the Python side can decide whether to run the external-change listener.
struct VolumeState {
  bool supported = false;
  int current = 0;
  int max = 100;
  int backend = static_cast<int>(VolumeBackend::None);
};

/// Hardware ALSA mixer control for a single card, plus a background thread that
/// watches for *external* changes (alsamixer, amixer, a hardware knob, another
/// app) and notifies subscribers — the local analogue of MusicCast pushing
/// volume events back to the UI.
///
/// All mixer access is serialized; get/set are safe to call from any thread.
class AlsaVolumeControl {
public:
  /// @param deviceName the PCM name from `output.alsa.device` (e.g. "default"
  ///        or "hw:CARD=sndrpihifiberry,DEV=0"); the mixer card is derived from
  ///        it.
  /// @param controlName overrides the simple-mixer element to use; empty means
  ///        auto-pick (Master → PCM → … → first element with playback volume).
  AlsaVolumeControl(const std::string &deviceName,
                    const std::string &controlName);
  ~AlsaVolumeControl();

  AlsaVolumeControl(const AlsaVolumeControl &) = delete;
  AlsaVolumeControl &operator=(const AlsaVolumeControl &) = delete;

  /// True when a usable playback-volume mixer element was found.
  bool available() const { return available_; }

  /// Current volume in 0..100, or -1 when unavailable.
  int getVolume();

  /// Set volume, clamped to 0..100. No-op when unavailable.
  void setVolume(int percent);

  /// Subscribe to external-change notifications. The callback runs on the
  /// monitor thread with the new 0..100 value. Returns an id for unsubscribe().
  int subscribe(std::function<void(int)> callback);
  void unsubscribe(int id);

private:
  bool openMixer(const std::string &deviceName, const std::string &controlName);
  void closeMixer();
  int rawToPercent(long raw) const;
  long percentToRaw(int percent) const;
  int readPercentLocked();
  void monitorLoop(std::stop_token token);
  void notify(int percent);

  std::mutex mixerMutex_;
  snd_mixer_t *mixer_ = nullptr;
  snd_mixer_elem_t *elem_ = nullptr;
  long rawMin_ = 0;
  long rawMax_ = 0;
  bool available_ = false;
  int lastNotified_ = -1;

  std::mutex subscribersMutex_;
  std::list<std::pair<int, std::function<void(int)>>> subscribers_;
  int nextSubscriberId_ = 0;

  int wakePipe_[2] = {-1, -1}; // self-pipe to break poll() on shutdown
  std::jthread monitorThread_;
};

/// Blocks until the hardware mixer changes — the volume analogue of
/// StateMonitor. Python awaits wait() on a worker thread (the GIL is released
/// while blocked). Constructing with a null/unavailable control yields an inert
/// monitor whose wait() returns immediately and isRunning() is false.
class VolumeMonitor {
public:
  explicit VolumeMonitor(AlsaVolumeControl *control);
  ~VolumeMonitor();

  VolumeMonitor(const VolumeMonitor &) = delete;
  VolumeMonitor &operator=(const VolumeMonitor &) = delete;

  /// Waits for and returns the next external change as a VolumeState. Once
  /// stopped, returns a state with supported=false.
  VolumeState wait();
  bool hasData();
  bool isRunning() const;
  void stop();

private:
  AlsaVolumeControl *control_;
  int subscriptionId_ = -1;
  std::mutex mutex_;
  std::condition_variable cv_;
  std::queue<int> queue_;
  bool stopped_ = false;
};

#endif // ALSA_VOLUME_CONTROL_H
