#!/usr/bin/env python3
"""Unit tests for the sldl offline input parser.

Run with: python3 -m unittest discover -s tests  (or: sldl self-test)
No network access or downloads are performed by any test.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import sldl_parser as p  # noqa: E402


class DetectInputTypeTest(unittest.TestCase):
    def test_slsk_link(self):
        self.assertEqual(p.detect_input_type("slsk://user/folder/"), "soulseek")

    def test_csv_path(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as fh:
            fh.write(b"Artist,Title\n")
            name = fh.name
        try:
            self.assertEqual(p.detect_input_type(name), "csv")
        finally:
            os.unlink(name)

    def test_list_path(self):
        with tempfile.NamedTemporaryFile(suffix=".list", delete=False) as fh:
            fh.write(b"foo\n")
            name = fh.name
        try:
            self.assertEqual(p.detect_input_type(name), "list")
        finally:
            os.unlink(name)

    def test_urls_classified_by_host(self):
        self.assertEqual(p.detect_input_type("https://www.youtube.com/playlist?list=x"), "youtube")
        self.assertEqual(p.detect_input_type("https://open.spotify.com/playlist/x"), "spotify")
        self.assertEqual(p.detect_input_type("https://musicbrainz.org/release/x"), "musicbrainz")

    def test_keyed_string(self):
        self.assertEqual(p.detect_input_type("title=X, artist=Y"), "string")


class CsvTest(unittest.TestCase):
    def _write(self, content):
        fd, name = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.addCleanup(os.unlink, name)
        return name

    def test_song_rows(self):
        path = self._write("Artist,Title,Album,Length\nDaft Punk,One More Time,Discovery,322\n")
        result = p.parse_csv(path)
        job = result["jobs"][0]
        self.assertEqual(job["kind"], "Song")
        self.assertEqual(job["artist"], "Daft Punk")
        self.assertEqual(job["title"], "One More Time")
        self.assertEqual(job["album"], "Discovery")
        self.assertEqual(job["length"], 322)

    def test_empty_title_is_album(self):
        path = self._write("Artist,Title,Album\nPink Floyd,,The Dark Side of the Moon\n")
        result = p.parse_csv(path)
        self.assertEqual(result["jobs"][0]["kind"], "Album")

    def test_no_title_column_is_album(self):
        path = self._write("Artist,Album\nRadiohead,OK Computer\n")
        result = p.parse_csv(path)
        self.assertEqual(result["jobs"][0]["kind"], "Album")
        self.assertEqual(result["jobs"][0]["album"], "OK Computer")

    def test_mixed_rows(self):
        path = self._write("Artist,Title,Album\nDaft Punk,One More Time,Discovery\nPink Floyd,,Dark Side\n")
        kinds = [j["kind"] for j in p.parse_csv(path)["jobs"]]
        self.assertEqual(kinds, ["Song", "Album"])

    def test_length_hms(self):
        path = self._write("Artist,Title,Length\nX,Y,1:04:35\n")
        self.assertEqual(p.parse_csv(path)["jobs"][0]["length"], 3875)

    def test_missing_file(self):
        with self.assertRaises(p.ParseError):
            p.parse_csv("/nonexistent/nope.csv")

    def test_header_detection(self):
        path = self._write("Artist Dove,Canzone,Album,Minuti\nA,B,C,3\n")
        result = p.parse_csv(path, title_col="Canzone", length_col="Minuti")
        self.assertEqual(result["jobs"][0]["title"], "B")


class StringTest(unittest.TestCase):
    def test_keyed_song(self):
        result = p.parse_string("artist=Daft Punk, title=One More Time, length=322")
        job = result["jobs"][0]
        self.assertEqual(job["kind"], "Song")
        self.assertEqual(job["artist"], "Daft Punk")
        self.assertEqual(job["length"], 322)

    def test_shorthand_album_default(self):
        result = p.parse_string("Radiohead - Karma Police")
        job = result["jobs"][0]
        self.assertEqual(job["kind"], "Album")
        self.assertEqual(job["album"], "Karma Police")

    def test_shorthand_song_mode(self):
        result = p.parse_string("Radiohead - Karma Police", song=True)
        job = result["jobs"][0]
        self.assertEqual(job["kind"], "Song")
        self.assertEqual(job["title"], "Karma Police")

    def test_unknown_property(self):
        with self.assertRaises(p.ParseError):
            p.parse_string("bogus=value")

    def test_freeform(self):
        result = p.parse_string("bjork joga")
        self.assertEqual(result["jobs"][0]["kind"], "Search")


class SlskTest(unittest.TestCase):
    def test_folder(self):
        result = p.parse_slsk("slsk://user/Shared/Folder/")
        self.assertEqual(result["jobs"][0]["kind"], "Album")
        self.assertTrue(result["jobs"][0]["folder"])

    def test_file(self):
        result = p.parse_slsk("slsk://user/Shared/Folder/file.flac")
        self.assertEqual(result["jobs"][0]["kind"], "Song")
        self.assertFalse(result["jobs"][0]["folder"])

    def test_bad(self):
        with self.assertRaises(p.ParseError):
            p.parse_slsk("https://x")


class UrlTest(unittest.TestCase):
    def test_spotify(self):
        result = p.parse_url("https://open.spotify.com/album/x")
        self.assertEqual(result["inputType"], "spotify")
        self.assertEqual(result["jobs"][0]["kind"], "Extract")


class ListTest(unittest.TestCase):
    def _write(self, content):
        fd, name = tempfile.mkstemp(suffix=".list")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.addCleanup(os.unlink, name)
        return name

    def test_lines(self):
        path = self._write("slsk://user/folder/\n# comment\n\nRadiohead - Karma Police\n")
        result = p.parse_list(path)
        kinds = [j["kind"] for j in result["jobs"]]
        self.assertEqual(kinds, ["Album", "Album"])


class NormalizeTest(unittest.TestCase):
    def test_force_type(self):
        result = p.normalize_input("slsk://u/f/", input_type="soulseek")
        self.assertEqual(result["inputType"], "soulseek")


if __name__ == "__main__":
    unittest.main()