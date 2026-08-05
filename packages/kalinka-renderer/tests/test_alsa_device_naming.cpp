#include <gtest/gtest.h>

#include "../src/native_player/AlsaDeviceEnumeration.h"

namespace {

const char *kHwDesc =
    "bcm2835 Headphones, bcm2835 Headphones\n"
    "Direct hardware device without any conversions";
const char *kHifiberryDesc =
    "snd_rpi_hifiberry_digi, HiFiBerry Digi+ Pro HiFi wm8804-spdif-0\n"
    "Hardware device with all software conversions";
const char *kHdmiDesc = "vc4-hdmi-1, MAI PCM i2s-hifi-0\nDefault Audio Device";

TEST(AlsaDeviceNaming, TheChipIdInFrontOfAConnectorGoesAway) {
  EXPECT_EQ(describeAlsaPcm("hw:CARD=Headphones,DEV=0", kHwDesc).label,
            "Headphones (direct)");
}

TEST(AlsaDeviceNaming, ADriverModuleIdYieldsToTheReadablePcmName) {
  const AlsaPcmDevice device =
      describeAlsaPcm("plughw:CARD=sndrpihifiberry,DEV=0", kHifiberryDesc);
  EXPECT_EQ(device.label, "HiFiBerry Digi+ Pro (converted)");
}

TEST(AlsaDeviceNaming, HdmiPortsAreCountedTheWayTheBoardIsLabelled) {
  EXPECT_EQ(describeAlsaPcm("default:CARD=vc4hdmi1", kHdmiDesc).label,
            "HDMI 2 (shared)");
}

TEST(AlsaDeviceNaming, ASecondSubdeviceStaysDistinguishable) {
  EXPECT_EQ(describeAlsaPcm("hw:CARD=USB,DEV=1",
                            "Topping D10, USB Audio #1\nDirect hardware device "
                            "without any conversions")
                .label,
            "Topping D10 #1 (direct)");
}

TEST(AlsaDeviceNaming, TheAccessModeDecidesWhatTheDescriptionSays) {
  EXPECT_NE(describeAlsaPcm("hw:CARD=Headphones,DEV=0", kHwDesc)
                .description.find("unchanged"),
            std::string::npos);
  EXPECT_NE(describeAlsaPcm("plughw:CARD=sndrpihifiberry,DEV=0", kHifiberryDesc)
                .description.find("software conversion"),
            std::string::npos);
}

TEST(AlsaDeviceNaming, TheDefaultAndTheNullSinkReadAsPlainEnglish) {
  EXPECT_EQ(describeAlsaPcm("default", "").label, "System default");
  EXPECT_EQ(describeAlsaPcm("null", "Discard all samples (playback)").label,
            "No output");
}

// "Default ALSA Output (currently PipeWire Media Server)" and friends.
TEST(AlsaDeviceNaming, AParentheticalAsideMovesToTheDescription) {
  const AlsaPcmDevice device = describeAlsaPcm(
      "default", "Default ALSA Output (currently PipeWire Media Server)");
  EXPECT_EQ(device.label, "System default");
  EXPECT_NE(device.description.find("Currently PipeWire Media Server."),
            std::string::npos);
}

TEST(AlsaDeviceNaming, AnAsideOnAnUnknownDeviceStillLeavesTheNameAlone) {
  const AlsaPcmDevice device =
      describeAlsaPcm("somedev", "Some Device (currently idle)\nA device");
  EXPECT_EQ(device.label, "Some Device");
  EXPECT_EQ(device.description, "A device. Currently idle.");
}

// No hint at all: a device configured before the card was unplugged.
TEST(AlsaDeviceNaming, ANameWithoutAHintStillGetsItsMode) {
  const AlsaPcmDevice device = describeAlsaPcm("hw:CARD=Gone,DEV=0", "");
  EXPECT_EQ(device.label, "hw:CARD=Gone,DEV=0 (direct)");
}

}  // namespace
