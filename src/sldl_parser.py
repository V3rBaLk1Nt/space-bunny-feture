#!/usr/bin/env python3
"""sldl_parser - offline input parser for Soulseek style jobs.

Parses the input forms accepted by the sockseek client (v3.0.5) into a
normalized JSON job tree, WITHOUT touching the network, performing searches,
or downloading anything. Mirrors the client's documented and observed
semantics:

  CSV file        Rows with a non-empty title are song jobs; rows without a
                  title are album jobs. Columns are auto-detected from common
                  names or set explicitly (--title-col etc).
  List file       One item per line: a search string, a slsk:// link, or a
                  path to another CSV/list file. Empty lines and lines
                  starting with '#' are ignored.
  Search string   Keyed comma-separated properties
                  (title, artist, album, length, artist-maybe-wrong,
                  album-track-count), or the "ARTIST - TITLE" shorthand
                  (album job by default, song job with --song).
  slsk:// link    Path ending in '/' is an album/folder job; otherwise a
                  single-file song job.
  URL             Classified by host (youtube/spotify/bandcamp/musicbrainz)
                  and represented as a job that requires network extraction
                  (never performed here).

Output is written as JSON to stdout, or to --json FILE.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from urllib.parse import urlparse

VERSION = "1.0.0"

KNOWN_URL_HOSTS = {
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "music.youtube.com": "youtube",
    "youtu.be": "youtube",
    "open.spotify.com": "spotify",
    "bandcamp.com": "bandcamp",
    "musicbrainz.org": "musicbrainz",
}

COMMON_COLUMNS = {
    "artist": {"artist", "artists", "performer", "performer(s)"},
    "title": {"title", "track", "track title", "song", "song title", "name"},
    "album": {"album", "release"},
    "length": {"length", "duration", "time", "durat"},
    "album_track_count": {
        "album-track-count",
        "album track count",
        "track count",
        "# tracks",
        "tracks",
        "albumtracks",
    },
}

KEYED_PROPS = {
    "title",
    "artist",
    "album",
    "length",
    "artist-maybe-wrong",
    "album-track-count",
}


class ParseError(Exception):
    """Raised for malformed or unsupported input."""


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    value = value.strip()
    return re.sub(r"\s+", " ", value)


def _parse_length(value: str) -> int | None:
    value = _clean(value)
    if not value:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return int(float(value))
    parts = value.split(":")
    if 2 <= len(parts) <= 3 and all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        nums = [float(p) for p in parts]
        if len(nums) == 2:
            return int(nums[0] * 60 + nums[1])
        return int(nums[0] * 3600 + nums[1] * 60 + nums[2])
    return None


def _resolve_column(header: str, requested: str | None, alias_set: set[str]) -> str | None:
    if requested:
        for h in header:
            if h.lower() == requested.lower():
                return h
        raise ParseError(f"requested column not found: {requested}")
    for h in header:
        if h.lower() in alias_set:
            return h
    return None


def detect_input_type(source: str) -> str:
    """Classify <source> as csv|list|string|soulseek|<url-host>."""
    s = source.strip()
    lowered = s.lower()
    if lowered.startswith("slsk://"):
        return "soulseek"
    parsed = urlparse(s)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host in KNOWN_URL_HOSTS:
            return KNOWN_URL_HOSTS[host]
        return "string"
    if lowered.endswith(".csv") and os.path.isfile(s):
        return "csv"
    if lowered.endswith((".list", ".txt", ".m3u")) and os.path.isfile(s):
        return "list"
    if os.path.isfile(s):
        return "csv" if s.lower().endswith(".csv") else "list"
    if "=" in s:
        return "string"
    return "string"


def parse_csv(path: str,
              artist_col: str | None = None,
              title_col: str | None = None,
              album_col: str | None = None,
              length_col: str | None = None,
              album_track_count_col: str | None = None) -> dict:
    if not os.path.isfile(path):
        raise ParseError(f"CSV file not found: {path}")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = [_clean(h) for h in next(reader)]
        except StopIteration as exc:
            raise ParseError(f"empty CSV file: {path}") from exc
        if not any(header):
            raise ParseError(f"CSV file has no header row: {path}")

        col_artist = _resolve_column(header, artist_col, COMMON_COLUMNS["artist"])
        col_title = _resolve_column(header, title_col, COMMON_COLUMNS["title"])
        col_album = _resolve_column(header, album_col, COMMON_COLUMNS["album"])
        col_length = _resolve_column(header, length_col, COMMON_COLUMNS["length"])
        col_atc = _resolve_column(header, album_track_count_col, COMMON_COLUMNS["album_track_count"])

        idx = {c: i for i, c in enumerate(header) if c}
        jobs = []
        for lineno, row in enumerate(reader, start=2):
            cells = [_clean(c) for c in row]
            cell = {
                "artist": cells[idx[col_artist]] if col_artist and col_artist in idx else "",
                "title": cells[idx[col_title]] if col_title and col_title in idx else "",
                "album": cells[idx[col_album]] if col_album and col_album in idx else "",
                "length": cells[idx[col_length]] if col_length and col_length in idx else "",
                "atc": cells[idx[col_atc]] if col_atc and col_atc in idx else "",
            }
            if not any(cell.values()):
                continue
            job: dict = {"kind": "Song" if cell["title"] else "Album"}
            if cell["artist"]:
                job["artist"] = cell["artist"]
            if cell["title"]:
                job["title"] = cell["title"]
            elif cell["album"]:
                job["display"] = cell["album"]
            if cell["album"]:
                job["album"] = cell["album"]
            length = _parse_length(cell["length"])
            if length is not None:
                job["length"] = length
            if cell["atc"]:
                job["albumTrackCount"] = cell["atc"]
            job["source"] = {"type": "csv", "file": path, "line": lineno}
            jobs.append(job)

    return {"input": path, "inputType": "csv", "jobs": jobs}


def _split_keyed(s: str) -> dict:
    """Parse 'k=v, k2=v2' into a dict. Values may contain spaces."""
    result: dict[str, str] = {}
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise ParseError(f"expected 'key=value', got: {token!r}")
        key, _, value = token.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key not in KEYED_PROPS:
            raise ParseError(f"unknown property {key!r} (accepted: {', '.join(sorted(KEYED_PROPS))})")
        result[key] = value
    return result


def _job_from_map(m: dict) -> dict:
    job: dict = {}
    has_title = bool(m.get("title"))
    has_album = bool(m.get("album"))
    if has_title or not has_album:
        job["kind"] = "Song"
    else:
        job["kind"] = "Album"
    if m.get("artist"):
        job["artist"] = m["artist"]
    if m.get("title"):
        job["title"] = m["title"]
    if m.get("album"):
        job["album"] = m["album"]
    if m.get("length"):
        length = _parse_length(m["length"])
        if length is None:
            raise ParseError(f"invalid length {m['length']!r}")
        job["length"] = length
    if m.get("artist-maybe-wrong") and m["artist-maybe-wrong"].lower() in ("true", "1", "yes"):
        job["artistMaybeWrong"] = True
    if m.get("album-track-count"):
        job["albumTrackCount"] = m["album-track-count"]
    return job


def parse_string(s: str, song: bool = False) -> dict:
    s = _clean(s)
    if not s:
        raise ParseError("empty string input")
    if s.lower().startswith("slsk://"):
        return parse_slsk(s)
    if "=" in s:
        job = _job_from_map(_split_keyed(s))
        return {"input": s, "inputType": "string", "jobs": [job]}
    m = re.match(r"^(.*?)\s*-\s*(.+)$", s)
    if m and " - " in s:
        artist, rest = m.group(1).strip(), m.group(2).strip()
        if song:
            job = _job_from_map({"artist": artist, "title": rest})
        else:
            job = _job_from_map({"artist": artist, "album": rest})
        return {"input": s, "inputType": "string", "jobs": [job]}
    # Free-form search string: no structured fields available.
    return {"input": s, "inputType": "string", "jobs": [{"kind": "Search", "query": s}]}


def parse_slsk(url: str) -> dict:
    u = _clean(url)
    if not u.lower().startswith("slsk://"):
        raise ParseError(f"not a slsk:// link: {u!r}")
    rest = u[len("slsk://"):]
    if not rest:
        raise ParseError(f"empty slsk:// link: {u!r}")
    is_folder = u.endswith("/")
    kind = "Album" if is_folder else "Song"
    return {
        "input": u,
        "inputType": "soulseek",
        "jobs": [{"kind": kind, "link": u, "folder": is_folder}],
    }


def parse_url(url: str) -> dict:
    parsed = urlparse(_clean(url))
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    input_type = KNOWN_URL_HOSTS.get(host, "string")
    return {"input": url, "inputType": input_type, "jobs": [{"kind": "Extract", "url": url}]}


def parse_list(path: str, opts: dict | None = None) -> dict:
    opts = opts or {}
    if not os.path.isfile(path):
        raise ParseError(f"list file not found: {path}")
    song = bool(opts.get("song"))
    jobs: list[dict] = []
    with open(path, encoding="utf-8-sig") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = _clean(raw)
            if not line or line.startswith("#") or line.isspace():
                continue
            if line.lower().startswith("slsk://"):
                jobs.append(parse_slsk(line)["jobs"][0])
            elif line.lower().endswith(".csv") and os.path.isfile(line):
                jobs.extend(parse_csv(line)["jobs"])
            elif line.lower().startswith(("http://", "https://")):
                jobs.append(parse_url(line)["jobs"][0])
            else:
                parsed = parse_string(line, song=song)
                jobs.extend(parsed["jobs"])
    return {"input": path, "inputType": "list", "jobs": jobs}


def normalize_input(source: str,
                    song: bool = False,
                    title_col: str | None = None,
                    album_col: str | None = None,
                    artist_col: str | None = None,
                    length_col: str | None = None,
                    album_track_count_col: str | None = None,
                    input_type: str | None = None) -> dict:
    kind = input_type or detect_input_type(source)
    if kind == "csv":
        result = parse_csv(source, album_col=album_col, title_col=title_col,
                           artist_col=artist_col, length_col=length_col,
                           album_track_count_col=album_track_count_col)
    elif kind == "list":
        result = parse_list(source, {"song": song})
    elif kind == "soulseek":
        result = parse_slsk(source)
    elif kind in ("youtube", "spotify", "bandcamp", "musicbrainz"):
        result = parse_url(source)
    elif kind == "string":
        result = parse_string(source, song=song)
    else:
        raise ParseError(f"cannot classify input: {source!r}")
    result["inputType"] = kind
    return result


def _fmt_job(job: dict) -> str:
    if job.get("kind") == "Song":
        parts = [job.get("artist", ""), job.get("title", "")]
        return "Song: " + " - ".join(p for p in parts if p) or job.get("display", "")
    if job.get("kind") == "Album":
        return "Album: " + (job.get("display") or job.get("album") or job.get("link", ""))
    if "link" in job:
        return f'{job.get("kind")}: {job["link"]}'
    return f'{job.get("kind")}: {job.get("url") or job.get("query", "")}'


def _print_human(result: dict) -> None:
    jobs = result["jobs"]
    print(f"{len(jobs)} job(s), input type: {result['inputType']}")
    for job in jobs:
        print("  " + _fmt_job(job))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sldl_parser", description=__doc__)
    ap.add_argument("input", help="CSV/list file path, slsk:// link, URL, or search string")
    ap.add_argument("--input-type", choices=["csv", "list", "soulseek", "youtube",
                                             "spotify", "bandcamp", "musicbrainz", "string"])
    ap.add_argument("-s", "--song", action="store_true", help="song mode for string shorthand")
    ap.add_argument("--title-col")
    ap.add_argument("--album-col")
    ap.add_argument("--artist-col")
    ap.add_argument("--length-col")
    ap.add_argument("--album-track-count-col")
    ap.add_argument("--json", metavar="FILE", help="write JSON job tree to FILE")
    ap.add_argument("--version", action="version", version=f"sldl_parser {VERSION}")
    ap.add_argument("--human", action="store_true", help="human-readable summary output")
    args = ap.parse_args(argv)

    try:
        result = normalize_input(
            args.input,
            song=args.song,
            input_type=args.input_type,
            title_col=args.title_col,
            album_col=args.album_col,
            artist_col=args.artist_col,
            length_col=args.length_col,
            album_track_count_col=args.album_track_count_col,
        )
    except ParseError as exc:
        print(f"sldl_parser: {exc}", file=sys.stderr)
        return 2

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"sldl_parser: wrote {args.json}", file=sys.stderr)

    if args.human:
        _print_human(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())