#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Module for extracting metadata tags from audio files (MP3, FLAC).
"""

import os
from typing import Dict, Any, Optional, Tuple
import logging
import base64

import mutagen
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC


class TagExtractor:
    """
    Class for extracting metadata tags from audio files.
    Currently supports MP3 and FLAC formats.
    """

    def __init__(self, file_path: str):
        """
        Initialize the TagExtractor with a path to an audio file.

        Args:
            file_path: Path to the audio file
        """
        self.file_path = file_path
        self.tags = None
        self.file_format = None

        if not os.path.exists(file_path):
            logging.error(f"File not found: {file_path}")
            return

        self._extract_tags()

    def _extract_tags(self) -> None:
        """
        Extract tags from the audio file based on its extension.
        """
        try:
            # Determine file format based on extension
            ext = os.path.splitext(self.file_path.lower())[1]

            if ext == ".mp3":
                self.file_format = "mp3"
                audio = MP3(self.file_path)
                self._process_mp3_tags(audio)
            elif ext == ".flac":
                self.file_format = "flac"
                audio = FLAC(self.file_path)
                self.tags = dict(audio)
            else:
                # Try to use mutagen's auto-detection for other formats
                try:
                    audio = mutagen.File(self.file_path)
                    if audio:
                        self.file_format = "other"
                        self.tags = dict(audio)
                    else:
                        logging.warning(f"Unsupported file format: {ext}")
                except Exception as e:
                    logging.error(f"Error auto-detecting file format: {str(e)}")

        except Exception as e:
            logging.error(f"Error extracting tags from {self.file_path}: {str(e)}")

    def _process_mp3_tags(self, audio: MP3) -> None:
        """
        Process MP3 tags into a more user-friendly format.

        Args:
            audio: MP3 object from mutagen
        """
        self.tags = {}

        # Handle ID3 tags
        if audio.tags:
            for key, value in audio.tags.items():
                # Convert ID3 frames to string values where possible
                if hasattr(value, "text"):
                    if value.text:
                        self.tags[key] = (
                            value.text[0] if len(value.text) == 1 else value.text
                        )
                else:
                    self.tags[key] = str(value)

        # Add audio properties
        if hasattr(audio.info, "length"):
            self.tags["duration"] = audio.info.length
        if hasattr(audio.info, "bitrate"):
            self.tags["bitrate"] = audio.info.bitrate
        if hasattr(audio.info, "sample_rate"):
            self.tags["sample_rate"] = audio.info.sample_rate

    def get_tags(self) -> Dict[str, Any]:
        """
        Get the extracted tags.

        Returns:
            Dictionary containing the extracted tags
        """
        return self.tags or {}

    def get_tag(self, tag_name: str, default: Any = None) -> Any:
        """
        Get a specific tag by name.

        Args:
            tag_name: Name of the tag to retrieve
            default: Default value to return if tag doesn't exist

        Returns:
            The tag value or default if not found
        """
        if not self.tags:
            return default
        return self.tags.get(tag_name, default)

    def get_file_format(self) -> Optional[str]:
        """
        Get the detected file format.

        Returns:
            String representing the file format ('mp3', 'flac', or 'other')
        """
        return self.file_format

    def get_musicbrainz_id(self, id_type: str = "track") -> Optional[str]:
        """
        Get a MusicBrainz ID from the file.

        Args:
            id_type: Type of ID to retrieve ('track', 'album', 'artist')

        Returns:
            MusicBrainz ID string or None if not available
        """
        if not self.tags:
            return None

        if self.file_format == "mp3":
            if id_type == "track":
                # Check for UFID frame first
                for key in self.tags:
                    if key.startswith("UFID:http://musicbrainz.org"):
                        return self.tags[key]
                # Fall back to TXXX frame
                return self.get_tag("TXXX:MusicBrainz Track Id")
            elif id_type == "album":
                return self.get_tag("TXXX:MusicBrainz Album Id")
            elif id_type == "artist":
                return self.get_tag("TXXX:MusicBrainz Artist Id")

        elif self.file_format in ["flac", "other"]:
            if id_type == "track":
                return self.get_tag("musicbrainz_trackid")
            elif id_type == "album":
                return self.get_tag("musicbrainz_albumid")
            elif id_type == "artist":
                return self.get_tag("musicbrainz_artistid")

        return None

    # Standard tag getter methods

    def get_title(self) -> Optional[str]:
        """
        Get the track title.

        Returns:
            Track title or None if not available
        """
        if not self.tags:
            return None

        # Handle different tag formats
        if self.file_format == "mp3":
            # Try common ID3 title tags
            for key in ["TIT2", "title"]:
                if key in self.tags:
                    return str(self.tags[key])
        elif self.file_format in ["flac", "other"]:
            # FLAC and other formats typically use lowercase keys
            if "title" in self.tags:
                return str(self.tags["title"])

        return None

    def get_album(self) -> Optional[str]:
        """
        Get the album title.

        Returns:
            Album title or None if not available
        """
        if not self.tags:
            return None

        if self.file_format == "mp3":
            # Try common ID3 album tags
            for key in ["TALB", "album"]:
                if key in self.tags:
                    return str(self.tags[key])
        elif self.file_format in ["flac", "other"]:
            if "album" in self.tags:
                return str(self.tags["album"])

        return None

    def get_artist(self) -> Optional[str]:
        """
        Get the artist/performer name.

        Returns:
            Artist name or None if not available
        """
        if not self.tags:
            return None

        if self.file_format == "mp3":
            # Try common ID3 artist tags in order of preference
            for key in ["TPE1", "TPE2", "artist", "performer"]:
                if key in self.tags:
                    return str(self.tags[key])
        elif self.file_format in ["flac", "other"]:
            # Try artist and then performer for FLAC
            for key in ["artist", "performer"]:
                if key in self.tags:
                    return str(self.tags["artist"])

        return None

    def get_year(self) -> Optional[str]:
        """
        Get the release year.

        Returns:
            Release year or None if not available
        """
        if not self.tags:
            return None

        if self.file_format == "mp3":
            # Try different year tag formats
            for key in ["TDRC", "TYER", "date"]:
                if key in self.tags:
                    return str(self.tags[key])
        elif self.file_format in ["flac", "other"]:
            for key in ["date", "year"]:
                if key in self.tags:
                    return str(self.tags[key])

        return None

    def get_duration(self) -> Optional[float]:
        """
        Get the track duration in seconds.

        Returns:
            Duration in seconds or None if not available
        """
        if not self.tags:
            return None

        # Duration is usually stored in the same way across formats
        if "duration" in self.tags:
            return float(self.tags["duration"])

        # Some formats might have length instead
        if "length" in self.tags:
            return float(self.tags["length"])

        return None

    def get_bitrate(self) -> Optional[int]:
        """
        Get the audio bitrate in kbps.

        Returns:
            Bitrate in kbps or None if not available
        """
        if not self.tags:
            return None

        if "bitrate" in self.tags:
            # Convert to kbps if needed
            bitrate = self.tags["bitrate"]
            if isinstance(bitrate, (int, float)):
                return int(bitrate / 1000) if bitrate > 1000 else int(bitrate)
            return None

        return None

    def get_sample_rate(self) -> Optional[int]:
        """
        Get the sample rate in Hz.

        Returns:
            Sample rate in Hz or None if not available
        """
        if not self.tags:
            return None

        if "sample_rate" in self.tags:
            rate = self.tags["sample_rate"]
            return int(rate) if isinstance(rate, (int, float)) else None

        return None

    def get_track_number(self) -> Optional[int]:
        """
        Get the track number.

        Returns:
            Track number or None if not available
        """
        if not self.tags:
            return None

        if self.file_format == "mp3":
            for key in ["TRCK", "tracknumber"]:
                if key in self.tags:
                    # Extract number from potential "X/Y" format
                    try:
                        track_str = str(self.tags[key]).split("/")[0]
                        return int(track_str)
                    except (ValueError, IndexError):
                        pass
        elif self.file_format in ["flac", "other"]:
            if "tracknumber" in self.tags:
                try:
                    track_str = str(self.tags["tracknumber"]).split("/")[0]
                    return int(track_str)
                except (ValueError, IndexError):
                    pass

        return None

    def get_genre(self) -> Optional[str]:
        """
        Get the genre.

        Returns:
            Genre or None if not available
        """
        if not self.tags:
            return None

        if self.file_format == "mp3":
            for key in ["TCON", "genre"]:
                if key in self.tags:
                    return str(self.tags[key])
        elif self.file_format in ["flac", "other"]:
            if "genre" in self.tags:
                return str(self.tags["genre"])

        return None

    def get_album_art(self) -> Optional[Tuple[bytes, str, str]]:
        """
        Extract album artwork from the audio file.

        Returns:
            Tuple containing (image_data, mime_type, description) or None if not available
            - image_data: The binary data of the image
            - mime_type: MIME type of the image (e.g., 'image/jpeg')
            - description: Optional description of the image
        """
        if not self.tags:
            return None

        try:
            # Handle MP3 (ID3 tags with APIC frames)
            if self.file_format == "mp3":
                audio = ID3(self.file_path)

                # Look for APIC frames (album art)
                for key in audio.keys():
                    if key.startswith("APIC"):
                        apic = audio[key]
                        return apic.data, apic.mime, apic.desc

                # If we get here, no APIC frame was found
                return None

            # Handle FLAC (uses PICTURE metadata blocks)
            elif self.file_format == "flac":
                audio = FLAC(self.file_path)

                # Check if pictures are available
                if audio.pictures:
                    # Usually the first picture is the cover art
                    picture = audio.pictures[0]
                    return picture.data, picture.mime, picture.desc

                # Alternative approach for FLAC files
                if "metadata_block_picture" in audio:
                    pic_data = audio["metadata_block_picture"][0]
                    picture = Picture(base64.b64decode(pic_data))
                    return picture.data, picture.mime, picture.desc

                return None

            # Handle other formats that might have embedded pictures
            elif self.file_format == "other":
                audio = mutagen.File(self.file_path)

                # Try different approaches depending on the actual format
                if hasattr(audio, "pictures") and audio.pictures:
                    picture = audio.pictures[0]
                    return picture.data, picture.mime, getattr(picture, "desc", "")

                # Some formats might store pictures differently
                for key in audio:
                    if (
                        key.lower().find("cover") >= 0
                        or key.lower().find("picture") >= 0
                    ):
                        pic_data = audio[key]
                        if isinstance(pic_data, bytes) or hasattr(pic_data, "data"):
                            data = (
                                pic_data
                                if isinstance(pic_data, bytes)
                                else pic_data.data
                            )
                            mime = "image/jpeg"  # Default guess
                            return data, mime, ""

                return None

        except Exception as e:
            logging.error(f"Error extracting album art from {self.file_path}: {str(e)}")
            return None

    def save_album_art(self, output_path: str, fail_if_exists=False) -> Optional[str]:
        """
        Save album artwork to a file with the correct extension based on MIME type.

        Args:
            output_path: Base path where the image file should be saved
                         (extension will be added automatically)
            fail_if_exists: If True, do not overwrite existing files

        Returns:
            Path with added extension if artwork was successfully saved, None otherwise
        """
        art_data = self.get_album_art()
        if not art_data:
            return None

        image_data, mime_type, _ = art_data

        # Ensure the directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as e:
                logging.error(f"Error creating directory {output_dir}: {str(e)}")
                return None

        # Map MIME types to file extensions
        mime_to_ext = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/tiff": ".tiff",
            "image/webp": ".webp",
        }

        # Get extension from MIME type or default to .jpg
        extension = mime_to_ext.get(mime_type.lower(), ".jpg")

        # Add extension if output_path doesn't already have it
        if not output_path.lower().endswith(extension):
            output_path_with_ext = output_path + extension
        else:
            output_path_with_ext = output_path

        try:
            # Check if file exists when fail_if_exists is True
            if fail_if_exists and os.path.exists(output_path_with_ext):
                logging.debug(f"File already exists: {output_path_with_ext}")
                return None

            with open(output_path_with_ext, "wb") as f:
                f.write(image_data)
            return output_path_with_ext
        except Exception as e:
            logging.error(f"Error saving album art to {output_path_with_ext}: {str(e)}")
            return None


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        extractor = TagExtractor(file_path)
        print(f"File: {file_path}")
        print(f"Format: {extractor.get_file_format()}")
        print("\nMain Information:")
        print(f"  Title: {extractor.get_title()}")
        print(f"  Album: {extractor.get_album()}")
        print(f"  Artist: {extractor.get_artist()}")
        print(f"  Year: {extractor.get_year()}")
    else:
        print("Usage: python tagextractor.py <audio_file_path>")
