#include <gtest/gtest.h>

#include <optional>
#include <string>

#include "player/StateTranslator.h"

namespace pb = kalinka::renderer::v1;
using state_translator::fillFormat;
using state_translator::fillPlaybackStateChanged;
using state_translator::fillVolume;
using state_translator::toProto;

TEST(StateTranslator, EveryGraphStateHasExactlyOneProtoState) {
  EXPECT_EQ(toProto(AudioGraphNodeState::STOPPED), pb::PLAYBACK_STATE_STOPPED);
  EXPECT_EQ(toProto(AudioGraphNodeState::PREPARING),
            pb::PLAYBACK_STATE_PREPARING);
  EXPECT_EQ(toProto(AudioGraphNodeState::STREAMING),
            pb::PLAYBACK_STATE_PLAYING);
  EXPECT_EQ(toProto(AudioGraphNodeState::PAUSED), pb::PLAYBACK_STATE_PAUSED);
  EXPECT_EQ(toProto(AudioGraphNodeState::FINISHED),
            pb::PLAYBACK_STATE_FINISHED);
  EXPECT_EQ(toProto(AudioGraphNodeState::ERROR), pb::PLAYBACK_STATE_ERROR);
  // A transition, not a state: reported as SourceChanged by the caller.
  EXPECT_EQ(toProto(AudioGraphNodeState::SOURCE_CHANGED),
            pb::PLAYBACK_STATE_UNSPECIFIED);
}

TEST(StateTranslator, EveryErrorSourceMaps) {
  EXPECT_EQ(toProto(StreamErrorSource::NONE), pb::ERROR_SOURCE_NONE);
  EXPECT_EQ(toProto(StreamErrorSource::HTTP_STREAM),
            pb::ERROR_SOURCE_HTTP_STREAM);
  EXPECT_EQ(toProto(StreamErrorSource::AUDIO_OUTPUT),
            pb::ERROR_SOURCE_AUDIO_OUTPUT);
  EXPECT_EQ(toProto(StreamErrorSource::DECODER), pb::ERROR_SOURCE_DECODER);
}

TEST(StateTranslator, EveryVolumeBackendMaps) {
  EXPECT_EQ(toProto(VolumeBackend::None), pb::VOLUME_BACKEND_NONE);
  EXPECT_EQ(toProto(VolumeBackend::Hardware), pb::VOLUME_BACKEND_HARDWARE);
  EXPECT_EQ(toProto(VolumeBackend::Software), pb::VOLUME_BACKEND_SOFTWARE);
}

TEST(StateTranslator, StreamingCarriesTokenAndLivePosition) {
  StreamState state(AudioGraphNodeState::STREAMING, 41750);
  pb::PlaybackStateChanged out;
  fillPlaybackStateChanged(state, "tok-7", 1234, out);

  EXPECT_EQ(out.state(), pb::PLAYBACK_STATE_PLAYING);
  EXPECT_EQ(out.position_ms(), 41750u);
  EXPECT_TRUE(out.position_valid());
  EXPECT_EQ(out.source_token(), "tok-7");
  EXPECT_EQ(out.at_unix_ms(), 1234);
  EXPECT_FALSE(out.has_error());
}

TEST(StateTranslator, StoppedWithoutTokenMeansTornDown) {
  StreamState state(AudioGraphNodeState::STOPPED);
  pb::PlaybackStateChanged out;
  fillPlaybackStateChanged(state, std::nullopt, 1234, out);

  EXPECT_EQ(out.state(), pb::PLAYBACK_STATE_STOPPED);
  EXPECT_FALSE(out.has_source_token());
  EXPECT_FALSE(out.position_valid());
}

TEST(StateTranslator, ErrorsCarrySourceAndFaultedToken) {
  StreamState state(AudioGraphNodeState::ERROR,
                    StreamError{StreamErrorSource::HTTP_STREAM, "410 gone"});
  pb::PlaybackStateChanged out;
  fillPlaybackStateChanged(state, "tok-7", 1234, out);

  EXPECT_EQ(out.state(), pb::PLAYBACK_STATE_ERROR);
  ASSERT_TRUE(out.has_error());
  EXPECT_EQ(out.error().source(), pb::ERROR_SOURCE_HTTP_STREAM);
  EXPECT_EQ(out.error().message(), "410 gone");
  EXPECT_EQ(out.error().source_token(), "tok-7");
}

TEST(StateTranslator, NegativePositionIsClampedNotWrapped) {
  StreamState state(AudioGraphNodeState::STREAMING, -1);
  pb::PlaybackStateChanged out;
  fillPlaybackStateChanged(state, std::nullopt, 0, out);
  EXPECT_EQ(out.position_ms(), 0u);
}

TEST(StateTranslator, FormatCarriesEveryField) {
  StreamInfo info;
  info.format = StreamAudioFormat{44100, 2, 16, AudioSampleFormat::PCM16_LE};
  info.streamType = StreamType::FRAMES;
  info.streamSize = 9876543;

  pb::AudioFormat out;
  fillFormat(info, out);

  EXPECT_EQ(out.sample_rate_hz(), 44100u);
  EXPECT_EQ(out.channels(), 2u);
  EXPECT_EQ(out.bits_per_sample(), 16u);
  EXPECT_EQ(out.sample_format(),
            sampleFormatToString(AudioSampleFormat::PCM16_LE));
  EXPECT_EQ(out.stream_kind(), pb::STREAM_KIND_FRAMES);
  EXPECT_EQ(out.stream_size_units(), 9876543u);
}

TEST(StateTranslator, VolumeCarriesEveryField) {
  VolumeState volume;
  volume.supported = true;
  volume.current = 42;
  volume.max = 100;
  volume.backend = static_cast<int>(VolumeBackend::Hardware);

  pb::VolumeState out;
  fillVolume(volume, out);

  EXPECT_TRUE(out.supported());
  EXPECT_EQ(out.current(), 42u);
  EXPECT_EQ(out.max(), 100u);
  EXPECT_EQ(out.backend(), pb::VOLUME_BACKEND_HARDWARE);
}
