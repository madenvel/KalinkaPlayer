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
from kalinka_plugin_sdk.inputmodule import TrackInfo, TrackUrl
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
    async def _link_retriever() -> TrackUrl:
        return TrackUrl(
            url=f"https://example.invalid/{track_id}.mp3", format="audio/mpeg"
        )

    return TrackInfo(
        id=_track_entity(track_id, source),
        metadata=_make_track(track_id, source),
        link_retriever=_link_retriever,
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


def test_restore_from_state_is_not_queue_wrapped():
    # serialised keeps async methods async; restore should stay undecorated.
    assert inspect.iscoroutinefunction(PlayQueueImpl.restore_from_state)
    assert inspect.iscoroutinefunction(PlayQueueImpl.add)
    assert not hasattr(PlayQueueImpl.restore_from_state, "__wrapped__")
    assert hasattr(PlayQueueImpl.add, "__wrapped__")
