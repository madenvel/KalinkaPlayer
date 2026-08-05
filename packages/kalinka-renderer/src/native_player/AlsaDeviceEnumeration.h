#pragma once

#include <string>
#include <vector>

/// Stable identifier + display text for an ALSA PCM device.
///
/// `name` is what's passed to `snd_pcm_open` (e.g. "default" or
/// "hw:CARD=sofhdadsp,DEV=0"). The CARD=<id> form is stable across
/// reboots and kernel upgrades because it references the kernel
/// driver's text id rather than the dynamic card index — using it
/// avoids the "hw:0,0 became hw:1,0 after upgrade" failure mode.
///
/// `label` names the device the way a person would, with the access
/// mode in brackets, because one card shows up several times.
/// `description` says what that mode costs — bit-perfect, resampled,
/// or shared.
///
/// `ioid` is "Output", "Input", or empty (= both). The Python layer
/// filters to outputs.
struct AlsaPcmDevice {
  std::string name;
  std::string label;
  std::string description;
  std::string ioid;
};

/// Turn an ALSA PCM name and its raw `DESC` hint into display text.
///
/// Exposed for tests and for naming a configured device that ALSA is
/// not currently reporting.
AlsaPcmDevice describeAlsaPcm(const std::string &name,
                              const std::string &alsaDescription);

/// Enumerate every PCM hint ALSA exposes for the current system.
///
/// Returns an empty vector if libasound is unavailable or the hint
/// API fails. The caller is responsible for filtering (e.g. dropping
/// the noisy `front:`/`surround*:`/`iec958:` virtual variants).
///
/// Fast — a few milliseconds typical — so safe to call per-request
/// when servicing /server/config.
std::vector<AlsaPcmDevice> listAlsaPcmDevices();
