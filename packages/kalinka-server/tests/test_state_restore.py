import inspect
from unittest.mock import AsyncMock, Mock

import pytest

from kalinka_plugin_sdk.datamodel import (
    Album,
    EntityId,
    EntityType,
    PlaybackMode,
    PlaybackState,
    PlayerStateEnum,
    Track,
)
from kalinka_plugin_sdk.events import PlayQueueState
from kalinka_plugin_sdk.inputmodule import DirectUrl, TrackInfo, TrackSource
from kalinka_serialized import ResolutionSlot
from kalinka_server import state_keeper
from kalinka_server.playqueue import PlayQueueImpl


def _track_entity(track_id: str, source: str) -> EntityId:
    return EntityId(id=track_id, type=EntityType.TRACK, source=source)


def _album_entity(album_id: str, source: str) -> EntityId:
    return EntityId(id=album_id, type=EntityType.ALBUM, source=source)


def _make_track(track_id: str, source: str) -> Track:
    return Track(
        id=_track_entity(track_id, source),
        title=f"track-{track_id}",
        duration=180,
        album=Album(id=_album_entity("album-1", source), title="album-1"),
    )


def _make_track_info(track_id: str, source: str) -> TrackInfo:
    async def _source_retriever() -> TrackSource:
        return TrackSource(
            source=DirectUrl(url=f"https://example.invalid/{track_id}.mp3"),
            format="audio/mpeg",
        )

    return TrackInfo(
        id=_track_entity(track_id, source),
        metadata=_make_track(track_id, source),
        source_retriever=_source_retriever,
    )


@pytest.mark.asyncio
async def test_restore_state_batches_track_info_requests(tmp_path, monkeypatch):
    state = PlayQueueState(
        playback_state=PlaybackState(state=PlayerStateEnum.STOPPED, index=1),
        playback_mode=PlaybackMode(
            shuffle=False,
            repeat_single=False,
            repeat_all=False,
        ),
        track_list=[
            _make_track("a1", "localfiles"),
            _make_track("a2", "localfiles"),
            _make_track("a1", "localfiles"),
            _make_track("b1", "other"),
        ],
    )

    state_file = tmp_path / "kalinka_state.json"
    state_file.write_text(state.model_dump_json())
    monkeypatch.setattr(state_keeper, "STATE_FILE", str(state_file))

    local_module = Mock()
    local_module.get_track_info = AsyncMock(
        return_value=[
            _make_track_info("a2", "localfiles"),
            _make_track_info("a1", "localfiles"),
        ]
    )
    other_module = Mock()
    other_module.get_track_info = AsyncMock(
        return_value=[_make_track_info("b1", "other")]
    )

    restored_ids: list[str] = []

    async def _capture_restore(received_state, track_info_retriever):
        for track in received_state.track_list:
            track_info = await track_info_retriever(track.id)
            restored_ids.append(track_info.id.to_string)

    playqueue = Mock()
    playqueue.restore_from_state = AsyncMock(side_effect=_capture_restore)

    await state_keeper.restore_state(
        playqueue,
        {
            "localfiles": local_module,
            "other": other_module,
        },
    )

    local_module.get_track_info.assert_awaited_once_with(["a1", "a2"])
    other_module.get_track_info.assert_awaited_once_with(["b1"])
    playqueue.restore_from_state.assert_awaited_once()
    assert restored_ids == [track.id.to_string for track in state.track_list]


@pytest.mark.asyncio
async def test_restore_prefers_saved_metadata_over_module():
    """On restore the saved snapshot is authoritative for metadata; the module's
    TrackInfo is used only for its playback source_retriever.

    This is what lets queue entries survive a restart even when a module can
    only re-resolve a playback URL but not the title/artist/album (e.g. Jamendo
    tracks whose ids the /tracks/ endpoint temporarily can't resolve)."""
    from types import SimpleNamespace

    saved = [_make_track("a1", "jamendo"), _make_track("a2", "jamendo")]
    state = PlayQueueState(
        playback_state=PlaybackState(state=PlayerStateEnum.STOPPED, index=0),
        playback_mode=PlaybackMode(
            shuffle=False, repeat_single=False, repeat_all=False
        ),
        track_list=saved,
    )

    # The module returns *placeholder* metadata (empty title) — simulating a
    # track it can only resolve a URL for, not real metadata.
    async def _placeholder_link():
        return TrackSource(
            source=DirectUrl(url="https://example.invalid/fallback.mp3"),
            format="audio/mpeg",
        )

    def _retriever_factory(tid):
        async def _retrieve(entity_id):
            return TrackInfo(
                id=entity_id,
                metadata=Track(
                    id=entity_id,
                    title="",  # placeholder — must NOT win
                    duration=0,
                    album=Album(
                        id=_album_entity("", "jamendo"), title=""
                    ),
                ),
                source_retriever=_placeholder_link,
            )

        return _retrieve

    added: list[TrackInfo] = []
    fake_self = SimpleNamespace(
        _track_player=SimpleNamespace(
            get_state=lambda: SimpleNamespace(state=None), stop=lambda: None
        ),
        _resolution=ResolutionSlot(),
        current_stream_id=None,
        track_list=[],
        prepared_tracks={},
        _unavailable_indices=set(),
        _cancel_prefetch_timer=lambda: None,
        current_track_id=0,
        shuffle=False,
        repeat_single=False,
        repeat_all=False,
        event_emitter=Mock(),
        _add=lambda infos: added.extend(infos),
    )

    await PlayQueueImpl.restore_from_state(
        fake_self, state, _retriever_factory(None)
    )

    assert [ti.metadata.title for ti in added] == ["track-a1", "track-a2"]
    # The playback link comes from the module's TrackInfo, not the saved Track.
    sources = [await ti.source_retriever() for ti in added]
    assert all(
        s.source.url == "https://example.invalid/fallback.mp3" for s in sources
    )


def test_restore_from_state_is_not_queue_wrapped():
    # serialised keeps async methods async; restore should stay undecorated.
    assert inspect.iscoroutinefunction(PlayQueueImpl.restore_from_state)
    assert inspect.iscoroutinefunction(PlayQueueImpl.add)
    assert not hasattr(PlayQueueImpl.restore_from_state, "__wrapped__")
    assert hasattr(PlayQueueImpl.add, "__wrapped__")
