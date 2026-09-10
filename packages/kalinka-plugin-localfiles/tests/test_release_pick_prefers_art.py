#!/usr/bin/env python3
"""Which pressing wins when the score cannot tell them apart.

The album score saturates at its 155-point ceiling on anything well
catalogued, so several pressings of one sleeve routinely tie and the winner
used to be whichever MusicBrainz happened to return first. That is how a
1979 German pressing — the only Abbey Road candidate the Cover Art Archive
has never photographed — beat a tied 1987 CD that it had.
"""

from kalinka_plugin_localfiles.enricher.musicbrainz_plugin import (
    ALBUM_MATCH_MIN_MARGIN,
    _has_front_art,
    _prefer_illustrated,
)


def _entry(release_id, score, rg="rg-abbey-road", front="true"):
    """One scored stage-B candidate: (cand, score, similarity, release)."""
    return (
        {"id": release_id, "release-group": {"id": rg}},
        score,
        1.0,
        {"id": release_id, "cover-art-archive": {"front": front, "artwork": front}},
    )


def _ids(scored):
    return [t[0]["id"] for t in scored]


class TestTheArtworkFlag:
    def test_the_strings_are_compared_not_tested(self):
        """MusicBrainz sends "true"/"false" — both truthy if merely tested."""
        assert _has_front_art({"cover-art-archive": {"front": "true"}})
        assert not _has_front_art({"cover-art-archive": {"front": "false"}})

    def test_a_release_with_no_archive_block_has_no_art(self):
        assert not _has_front_art({})
        assert not _has_front_art({"cover-art-archive": None})


class TestPreferringAnIllustratedPressing:
    def test_a_tied_pressing_with_art_beats_one_without(self):
        scored = [_entry("de-1979", 155.0, front="false"), _entry("gb-1987", 155.0)]
        assert _ids(_prefer_illustrated(scored)) == ["gb-1987", "de-1979"]

    def test_the_top_pick_is_left_alone_when_it_already_has_art(self):
        scored = [_entry("gb-1987", 155.0), _entry("de-1979", 155.0, front="false")]
        assert _ids(_prefer_illustrated(scored)) == ["gb-1987", "de-1979"]

    def test_a_lower_scoring_release_never_wins_on_art_alone(self):
        """Outside the margin the score has genuinely decided; art must not
        overturn a match that is actually better evidenced."""
        behind = 155.0 - ALBUM_MATCH_MIN_MARGIN - 0.1
        scored = [_entry("de-1979", 155.0, front="false"), _entry("gb-1987", behind)]
        assert _ids(_prefer_illustrated(scored)) == ["de-1979", "gb-1987"]

    def test_another_album_is_never_substituted(self):
        """Only pressings of one release group are interchangeable — promoting
        across groups would swap the album, not the pressing."""
        scored = [
            _entry("de-1979", 155.0, front="false"),
            _entry("other", 155.0, rg="rg-something-else"),
        ]
        assert _ids(_prefer_illustrated(scored)) == ["de-1979", "other"]

    def test_no_art_anywhere_keeps_the_score_order(self):
        scored = [
            _entry("de-1979", 155.0, front="false"),
            _entry("gb-1987", 155.0, front="false"),
        ]
        assert _ids(_prefer_illustrated(scored)) == ["de-1979", "gb-1987"]

    def test_a_lone_candidate_is_returned_unchanged(self):
        scored = [_entry("de-1979", 155.0, front="false")]
        assert _ids(_prefer_illustrated(scored)) == ["de-1979"]

    def test_the_rest_of_the_order_survives_promotion(self):
        scored = [
            _entry("de-1979", 155.0, front="false"),
            _entry("nl-1983", 154.0, front="false"),
            _entry("gb-1987", 153.5),
        ]
        assert _ids(_prefer_illustrated(scored)) == ["gb-1987", "de-1979", "nl-1983"]
