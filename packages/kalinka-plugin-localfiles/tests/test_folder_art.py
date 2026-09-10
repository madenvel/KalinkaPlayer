#!/usr/bin/env python3
"""Choosing an album's cover out of the images filed beside it.

The names in a real scan set say nothing useful — ``beatles_abbey_1.jpg`` is
the sleeve and ``beatles_abbey_d1.jpg`` is a disc label — and every scan is
square, so neither the name nor the shape decides. What separates them is
physical size: at one scan resolution a 31 cm sleeve is about three times a
10 cm label.
"""

import os

import pytest
from PIL import Image

from kalinka_plugin_localfiles.utils.folder_art import find_folder_cover


def _image(path, size=(1000, 1000)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, (90, 120, 160)).save(path)
    return path


def _name(path):
    return os.path.basename(path) if path else None


class TestWhatIsAdmissible:
    def test_an_empty_folder_offers_nothing(self, tmp_path):
        assert find_folder_cover(str(tmp_path)) is None

    def test_a_missing_folder_is_not_an_error(self, tmp_path):
        assert find_folder_cover(str(tmp_path / "nope")) is None
        assert find_folder_cover("") is None

    def test_audio_and_text_are_not_covers(self, tmp_path):
        (tmp_path / "01.flac").write_bytes(b"not audio really")
        (tmp_path / "notes.txt").write_text("hello")
        assert find_folder_cover(str(tmp_path)) is None

    def test_an_archival_tiff_is_never_chosen(self, tmp_path):
        """A sleeve TIFF runs to hundreds of megabytes; a player has no use
        for one and decoding it on a Pi is not worth the cover."""
        _image(str(tmp_path / "sleeve_front.tif"), (4000, 4000))
        assert find_folder_cover(str(tmp_path)) is None

    def test_a_tiff_does_not_displace_a_jpeg(self, tmp_path):
        _image(str(tmp_path / "scan_1.tif"), (4000, 4000))
        _image(str(tmp_path / "scan_2.jpg"), (900, 900))
        assert _name(find_folder_cover(str(tmp_path))) == "scan_2.jpg"

    def test_an_oversized_file_is_skipped_without_being_opened(
        self, tmp_path, monkeypatch
    ):
        import kalinka_plugin_localfiles.utils.folder_art as folder_art

        _image(str(tmp_path / "cover.jpg"))
        monkeypatch.setattr(folder_art, "_MAX_BYTES", 10)
        monkeypatch.setattr(
            folder_art, "_measure", lambda p: pytest.fail("file was opened")
        )
        assert find_folder_cover(str(tmp_path)) is None

    def test_an_oversized_image_is_skipped_from_its_header(
        self, tmp_path, monkeypatch
    ):
        """A byte cap does not bound decode cost, so the pixel count is
        checked too — from the header, before any pixel is read."""
        import kalinka_plugin_localfiles.utils.folder_art as folder_art

        _image(str(tmp_path / "cover.jpg"), (1200, 1200))
        monkeypatch.setattr(folder_art, "_MAX_PIXELS", 1000)
        assert find_folder_cover(str(tmp_path)) is None

    def test_a_gif_is_admissible(self, tmp_path):
        """Rare, but sometimes the only art a rip shipped."""
        _image(str(tmp_path / "pic.gif"), (400, 400))
        assert _name(find_folder_cover(str(tmp_path))) == "pic.gif"

    def test_the_header_decides_the_format_not_the_name(self, tmp_path):
        """Rips misname things: a real library had a JPEG called "pic.gif"."""
        path = str(tmp_path / "pic.gif")
        Image.new("RGB", (400, 400), (10, 20, 30)).save(path, "JPEG")
        assert _name(find_folder_cover(str(tmp_path))) == "pic.gif"

    def test_a_corrupt_image_is_ignored(self, tmp_path):
        (tmp_path / "cover.jpg").write_bytes(b"\xff\xd8 not really a jpeg")
        _image(str(tmp_path / "art_1.jpg"))
        assert _name(find_folder_cover(str(tmp_path))) == "art_1.jpg"


class TestTheNameWhenItSaysSomething:
    @pytest.mark.parametrize(
        "name", ["cover.jpg", "front.png", "folder.jpg", "AlbumArt.jpg", "Sleeve.jpg"]
    )
    def test_a_name_that_claims_to_be_the_cover_wins(self, tmp_path, name):
        _image(str(tmp_path / "scan_1.jpg"), (2000, 2000))
        _image(str(tmp_path / name), (600, 600))
        assert _name(find_folder_cover(str(tmp_path))) == name

    @pytest.mark.parametrize(
        "name", ["back.jpg", "cd1.jpg", "booklet.jpg", "disc.jpg", "spine.jpg"]
    )
    def test_a_name_that_disclaims_it_is_dropped(self, tmp_path, name):
        _image(str(tmp_path / name))
        assert find_folder_cover(str(tmp_path)) is None

    def test_the_largest_of_several_cover_names_wins(self, tmp_path):
        _image(str(tmp_path / "cover_small.jpg"), (300, 300))
        _image(str(tmp_path / "cover_big.jpg"), (1500, 1500))
        assert _name(find_folder_cover(str(tmp_path))) == "cover_big.jpg"


class TestWhenTheNamesAreNoHelp:
    def test_a_disc_label_loses_to_the_sleeve_on_physical_size(self, tmp_path):
        """The Abbey Road case: both square, both unhelpfully named, and the
        label is a third of the sleeve because that is how big it really is."""
        _image(str(tmp_path / "abbey_1.jpg"), (7439, 7405))
        _image(str(tmp_path / "abbey_d1.jpg"), (2384, 2387))
        assert _name(find_folder_cover(str(tmp_path))) == "abbey_1.jpg"

    def test_the_lower_ordinal_breaks_a_front_and_back_tie(self, tmp_path):
        _image(str(tmp_path / "scan_2.jpg"), (2000, 2000))
        _image(str(tmp_path / "scan_1.jpg"), (2000, 2000))
        assert _name(find_folder_cover(str(tmp_path))) == "scan_1.jpg"

    def test_a_spine_is_not_a_cover(self, tmp_path):
        _image(str(tmp_path / "img_1.jpg"), (2400, 200))
        assert find_folder_cover(str(tmp_path)) is None

    def test_a_scan_border_does_not_disqualify_a_cover(self, tmp_path):
        _image(str(tmp_path / "img_1.jpg"), (1000, 1180))
        assert _name(find_folder_cover(str(tmp_path))) == "img_1.jpg"

    def test_the_choice_does_not_depend_on_listing_order(self, tmp_path):
        for name in ("c.jpg", "a.jpg", "b.jpg"):
            _image(str(tmp_path / name), (1200, 1200))
        assert _name(find_folder_cover(str(tmp_path))) == "a.jpg"


class TestSubdirectories:
    def test_a_scan_folder_one_level_down_is_searched(self, tmp_path):
        _image(str(tmp_path / "PIC" / "abbey_1.jpg"), (3000, 3000))
        assert _name(find_folder_cover(str(tmp_path))) == "abbey_1.jpg"

    def test_two_levels_down_is_too_far(self, tmp_path):
        _image(str(tmp_path / "extras" / "scans" / "front.jpg"))
        assert find_folder_cover(str(tmp_path)) is None
