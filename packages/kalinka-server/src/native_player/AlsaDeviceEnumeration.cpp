#include "AlsaDeviceEnumeration.h"

#include <alsa/asoundlib.h>

#include <cstdlib>
#include <memory>
#include <string>

namespace {

// snd_device_name_get_hint() returns malloc'd strings. RAII so we
// don't leak when filtering early.
struct CFree {
  void operator()(char *p) const noexcept {
    if (p) std::free(p);
  }
};
using CString = std::unique_ptr<char, CFree>;

CString getHint(const void *hint, const char *key) {
  return CString(snd_device_name_get_hint(hint, key));
}

// ALSA's DESC reads as "<card name>\n<pcm name>"; flatten to a
// single line so the client can render it in a constrained row.
std::string flattenDesc(const char *raw) {
  if (!raw) return {};
  std::string out;
  out.reserve(std::char_traits<char>::length(raw));
  bool just_break = false;
  for (const char *p = raw; *p; ++p) {
    if (*p == '\n' || *p == '\r') {
      if (!just_break && !out.empty()) {
        out += " \xc2\xb7 ";  // " · " — middle-dot separator
        just_break = true;
      }
    } else {
      out += *p;
      just_break = false;
    }
  }
  return out;
}

}  // namespace

std::vector<AlsaPcmDevice> listAlsaPcmDevices() {
  std::vector<AlsaPcmDevice> out;
  void **hints = nullptr;
  // -1 == all cards; "pcm" == interface family.
  if (snd_device_name_hint(-1, "pcm", &hints) < 0 || !hints) {
    return out;
  }
  for (void **h = hints; *h; ++h) {
    CString name = getHint(*h, "NAME");
    CString desc = getHint(*h, "DESC");
    CString ioid = getHint(*h, "IOID");
    if (!name) continue;
    out.push_back({
        std::string(name.get()),
        flattenDesc(desc.get()),
        ioid ? std::string(ioid.get()) : std::string(),
    });
  }
  snd_device_name_free_hint(hints);
  return out;
}
