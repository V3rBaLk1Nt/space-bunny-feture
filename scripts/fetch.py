#!/usr/bin/env python3
"""Retry-until-complete supervisor for the Soulseek downloader.

The client already retries a stale transfer against a different peer, but it
gives up permanently when a search returns nothing, and it never re-runs a
batch. This wraps it: run, read the client's own _index.csv manifest, keep only
what is still missing, escalate the search for the ones that found nothing, and
repeat until the batch is complete or the round budget runs out.

A track counts as finished only when the manifest says state 3 *and* the file
is present and non-empty.

    scripts/fetch.py lists/Born_On_Road_Crossy_Current_2026-09-19.csv
    scripts/fetch.py tracklist.txt --rounds 5
    scripts/fetch.py tracklist.txt --quality 128
    scripts/fetch.py tracklist.txt --dry-run

Escalation ladder
-----------------
A failed track walks up a fixed ladder. The artist is NEVER discarded until the
final rung, which exists only because a title-only search occasionally is the
only thing left to try. An earlier version of this file dropped the artist at
rung 1, which searched "flow" with no artist at all and could only ever return
noise. Anything found by that last rung is flagged `unverified` in the failure
report, because a title-only match may well be a different track.

    0  artist - title                      the proven shape
    1  artist - title + version words      "remix", "vip", "bootleg", ...
    2  other artist - title                collabs listed as A x B
    3  artist - first title words          longest title wins; Soulseek
                                           matches on word order
    4  title only                          last resort, flagged unverified
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME / "src"))

RUN_SH = HOME / "run.sh"
LOG_PATH = HOME / "logs" / "sldl.log"
INDEX = "_index.csv"

# state 3 is the client's "completed" marker; failurereason 9 is
# "no search results", the case a looser query can actually fix.
STATE_DONE = "3"
REASON_NO_RESULTS = "9"

BACKOFF = [0, 45, 150, 420, 900]

# The client names its output folder after the input file's stem, so every
# round used to land in its own roundNN/ directory. That is how one track ended
# up on disk five times under five folders. A constant stem puts every round in
# the same folder, where the client skips files it already has.
JOB_STEM = "queue"

AUDIO_SUFFIX = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aac", ".opus"}

QUALITY_COND = {
    "320": "format=mp3; br=320",
    "128": "format=mp3; br=128",
    "flac": "format=flac",
    "any": "format=mp3",
}

# Words that mark a specific version of a track. Stripping them is what made
# "Dolly My Baby (Remix)" unsearchable, so they are kept as a fallback rung
# rather than deleted up front.
VERSION_WORDS = r"(?:remix|mix|edit|version|ver|bootleg|vip|dub|extended|original|radio|rework|flip|cover|live|acoustic|instrumental|reprise)"

# Filler words that carry no search signal. Deliberately does NOT include the
# version markers: "remix" and "vip" are exactly the words a version-specific
# search needs, and they must survive into the rung 1 query.
STOP = frozenset("""a an the of and or to in on at for with from
ft feat featuring prod""".split())


def clean(text: str) -> str:
    """Strip everything Soulseek will not index.

    The network matches on plain words. Commas, brackets, quotes, ampersands,
    colons, digits and 'feat.' clauses all make a query fail outright, and they
    are also what made the client report 'Unknown condition' rather than
    searching. Only letters and spaces survive.
    """
    text = (text or "").lower()
    text = re.sub(r"\(.*?\)|\[.*?\]|\{.*?\}", " ", text)   # bracketed qualifiers
    text = re.sub(r"\b(feat|ft|featuring|with|prod)\b.*", " ", text)
    text = re.sub(r"[^a-z ]+", " ", text)                    # commas, digits, symbols
    return re.sub(r"\s+", " ", text).strip()


def version_words(title: str) -> str:
    """The version markers in a title, as bare search words.

    'Dolly My Baby (Remix) (VIP)' -> 'remix vip'. Returns '' when the title is
    the plain version, so rung 1 is skipped rather than run as a duplicate of
    rung 0.
    """
    found = re.findall(VERSION_WORDS, (title or "").lower())
    out: list[str] = []
    for w in found:
        w = re.sub(r"[^a-z]", "", w)
        if w and w not in STOP and w not in out:
            out.append(w)
    return " ".join(out)


def split_artists(artist: str) -> list[str]:
    raw = re.split(r"\s*(?:;|/|\bx\b|\bfeat\.?|\bft\.?|\bvs\.?)\s*", artist or "",
                   flags=re.I)
    parts = []
    for p in raw:
        c = clean(p)
        if c and c not in parts:
            parts.append(c)
    return parts


def variants(artist: str, title: str) -> list[tuple[str, str, bool]]:
    """The escalation ladder as (artist_query, title_query, unverified) rungs.

    Rungs that would repeat an earlier query are dropped, so a title with no
    version markers does not search "supercat - dolly my baby" twice.
    """
    t_base = clean(re.split(r"\(|\[", title or "")[0])
    t_full = clean(title)
    vers = version_words(title)
    people = split_artists(artist)
    a0 = people[0] if people else ""
    a1 = people[-1] if len(people) > 1 else ""

    ladder: list[tuple[str, str, bool]] = [(a0, t_base, False)]
    if a0 and vers:
        ladder.append((a0, f"{t_base} {vers}".strip(), False))
    if a1 and a1 != a0:
        ladder.append((a1, t_base, False))
    if a0 and t_full and t_full != t_base:
        ladder.append((a0, t_full, False))
    if t_base:
        ladder.append(("", t_base, True))

    seen, uniq = set(), []
    for a, t, flag in ladder:
        if not t:
            continue
        key = (a, t)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((a, t, flag))
    return uniq or [("", t_base or clean(title), True)]


def last_safe_rung(track: dict) -> int:
    """Index of the deepest rung that still carries an artist.

    A title-only rung cannot be validated: --strict-artist has no artist to
    require, so anything the network offers passes. In testing, "airplane
    mode" pulled Envy and Flobots and "father time" pulled Kendrick Lamar,
    each of which then counted as a successful download. So the rung is not
    merely rejected after the fact, it is never searched unless the caller
    explicitly asks with --allow-unverified.
    """
    for i in range(len(track["ladder"]) - 1, -1, -1):
        if not track["ladder"][i][2]:
            return i
    return 0


def load_tracks(source: Path) -> list[dict]:
    """Reuse the project's own parser so column detection stays consistent."""
    import sldl_parser as parser
    result = parser.normalize_input(str(source))
    tracks = []
    for job in result.get("jobs", []):
        if job.get("kind") != "Song":
            continue
        artist = job.get("artist", "") or ""
        title = job.get("title", "") or ""
        if not title or not clean(title):
            # placeholders such as "[Acapella Track]" are not real tracks
            continue
        ladder = variants(artist, title)
        tracks.append({
            "uid": len(tracks),
            "artist": artist,
            "title": title,
            "people": split_artists(artist),
            "ladder": ladder,
            "rung": 0,            # index into ladder
            "rounds": 0,          # how many rounds actually searched for it
            "reason": "",         # last failurereason seen
            "observed": "",       # last length the network offered
        })
    return tracks


def read_manifest(outdir: Path) -> dict[tuple[str, str], dict]:
    """(artist, title) -> manifest row, unioned over every _index.csv on disk.

    The client writes one index per job folder, and the job folder is named
    after the input file's stem. Reading only <outdir>/_index.csv therefore
    finds nothing, which made every track look permanently missing and turned
    five rounds into five identical searches. Every index under the output
    directory is read instead, and a later complete row beats an earlier
    incomplete one.
    """
    out: dict[tuple[str, str], dict] = {}
    indexes = [p for p in outdir.rglob(INDEX) if p.is_file()]
    for path in sorted(indexes, key=lambda p: p.stat().st_mtime):
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                key = ((row.get("artist") or "").strip(),
                       (row.get("title") or "").strip())
                prev = out.get(key)
                if prev is None or row.get("state") == STATE_DONE:
                    out[key] = row
    return out


def newest_index(outdir: Path) -> Path | None:
    """The index written by the round that just finished."""
    found = [p for p in outdir.rglob(INDEX) if p.is_file()]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def index_rows(path: Path) -> list[dict]:
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def row_has_file(row: dict, outdir: Path) -> bool:
    """True when the manifest's filepath is really on disk and non-empty.

    The manifest is written as the run progresses, so a track can finish after
    its row was last written: Detonation ended state 1 with reason 0 while the
    completed file sat on disk. Trusting state 3 alone undercounts. A search
    that matched nothing is the one case worth excluding, because by
    definition it has no file.
    """
    if not row:
        return False
    if row.get("failurereason") == REASON_NO_RESULTS:
        return False
    rel = (row.get("filepath") or "").lstrip("./")
    if not rel:
        return False
    for base in _job_dirs(outdir):
        try:
            p = base / rel
            if p.is_file() and p.stat().st_size > 0:
                return True
        except OSError:
            pass
    return False


def _job_dirs(outdir: Path) -> list[Path]:
    """Every folder the client may have written a finished file into."""
    dirs = [outdir]
    dirs += [p for p in outdir.iterdir() if p.is_dir()] if outdir.is_dir() else []
    return dirs


def clear_partials(outdir: Path) -> int:
    """Remove abandoned .incomplete files so a retry is not blocked by them."""
    n = 0
    for p in outdir.rglob("*.incomplete"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def flatten_round(outdir: Path, index: Path | None) -> int:
    """Lift a round's job folder up into the output root and drop the folder.

    Whatever the client decides to nest, the finished audio ends up in one
    place. This is what stops round02..round05 from re-fetching and re-storing
    everything round01 already has.
    """
    if index is None:
        return 0
    job_dir = index.parent
    if job_dir == outdir:
        return 0
    moved = 0
    for src in list(job_dir.iterdir()):
        dst = outdir / src.name
        try:
            if src.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
                shutil.move(str(src), str(dst))
            elif dst.exists():
                src.unlink()               # same track, already have it
            else:
                shutil.move(str(src), str(dst))
            moved += 1
        except OSError:
            pass
    try:
        job_dir.rmdir()
    except OSError:
        pass
    return moved


def write_query_csv(tracks: list[dict], dest: Path) -> dict[tuple[str, str], dict]:
    """Emit this round's queries and return (artist, title) -> track.

    The CSV input path is the one verified to work end to end; the list path
    aborts inside sldl's parser. The client echoes the sanitised query it was
    given straight back into its index, so the pair written here is the key the
    round's manifest rows come back under.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    back: dict[tuple[str, str], dict] = {}
    with dest.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["Artist", "Track"])
        for t in tracks:
            a, ti, _ = t["ladder"][t["rung"]]
            wr.writerow([a, ti])
            back.setdefault((a, ti), t)
    return back


def stale_peers(log: Path, tracks: list[dict]) -> list[str]:
    """Peers that went stale on one of these tracks, for --banned-users.

    This is the "try a different person" part. The client picks the peer and
    retries the same dead one up to --max-retries times, so a single idle
    uploader can eat a whole round. Naming the offenders and banning them for
    the next round turns that into a fresh set of candidates instead.

    Only peers that failed are banned. Nobody is banned for a transfer that
    worked, and nothing here is reported anywhere off this machine.
    """
    try:
        with log.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-4000:]
    except OSError:
        return []
    banned: list[str] = []
    for line in lines:
        if "StaleDownloadException" not in line and "Download attempt" not in line:
            continue
        m = re.search(r"transfer activity: ([^\\]+)\\", line) or \
            re.search(r"from '([^\\']+)\\", line)
        if not m:
            continue
        peer = m.group(1).strip()
        if peer and peer.lower() not in [b.lower() for b in banned]:
            banned.append(peer)
    return banned


def run_once(listfile: Path, outdir: Path, dry_run: bool, quality: str,
             banned: list[str] | None = None) -> int:
    if dry_run:
        with listfile.open(encoding="utf-8") as fh:
            n = max(0, sum(1 for _ in csv.reader(fh)) - 1)
        print(f"[dry-run] would search {n} queries")
        return 0
    cmd = [str(RUN_SH), "run", str(listfile), "--input-type", "csv",
           "--output-dir", str(outdir)]
    if quality in QUALITY_COND and quality != "320":
        cmd += ["--quality", quality]
    if banned:
        cmd += ["--banned-users", ",".join(banned)]
    print(f"[fetch] $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(HOME), capture_output=True, text=True)
    tail = (proc.stdout or "") + (proc.stderr or "")
    for line in tail.splitlines()[-6:]:
        print(f"[client] {line}")
    return proc.returncode


def track_rows(track: dict, rows: dict[tuple[str, str], dict],
               claimed: dict | None = None) -> list[dict]:
    """Manifest rows for every rung this track has already tried.

    Matching is by the query that was issued, not by the source metadata: once
    a track is on rung 3 its title in the manifest is the rung-3 title, not the
    one from the source list.
    """
    out = []
    for a, ti, _ in track["ladder"][: track["rung"] + 1]:
        key = (a, ti)
        if claimed is not None and claimed.get(key, track["uid"]) != track["uid"]:
            # Another track already owns this query. "BONES - Airplane Mode"
            # and "BONES - Airplane Mode (Drum and Bass Remix)" both start at
            # the query ('bones', 'airplane mode'), and without this a single
            # download was counted as finishing both of them.
            continue
        row = rows.get(key)
        if row:
            if claimed is not None:
                # First track to ask for a query owns it for the whole run.
                claimed.setdefault(key, track["uid"])
            out.append(row)
    return out


def harvest(track: dict, rows: dict[tuple[str, str], dict], outdir: Path,
            allow_unverified: bool, claimed: dict | None = None) -> None:
    """Fold manifest rows back onto a track and decide whether it is done.

    A hit on a title-only rung does not count as done unless the caller opted
    in. The client searches on the title and only uses the artist to rank, so a
    title-only rung will happily return a different artist's recording of a song
    with the same name -- observed in testing: "BONES - Airplane Mode" matched
    Borgore, Flobots and Fizzikx, and "Father Time" matched Kendrick Lamar.
    Those files are moved to unverified/ rather than accepted, because a wrong
    track that looks right is worse than a miss.
    """
    hits = track_rows(track, rows, claimed)
    if not hits:
        track["done"] = False
        return
    track["done"] = any(row_has_file(r, outdir) for r in hits)
    # report the most recent thing we learned about it
    last = hits[-1]
    track["reason"] = last.get("failurereason") or ""
    if last.get("length") and last["length"] not in ("-1", "", "0"):
        track["observed"] = last["length"]
    if track.get("quarantined"):
        track["observed"] = measure(track["quarantined"], outdir)

    if not track["done"]:
        return
    rung_hit = next((r for r in track_rows(track, rows, claimed)
                     if row_has_file(r, outdir)), None)
    if rung_hit is None:
        track["done"] = False
        return
    key = ((rung_hit.get("artist") or "").strip(),
           (rung_hit.get("title") or "").strip())
    _a, _t, unverified = track["ladder"][min(track["rung"], len(track["ladder"]) - 1)]
    if key[0] == "" or unverified:
        if allow_unverified:
            return
        moved = quarantine(track, outdir)
        track["done"] = False
        track["quarantined"] = moved


def measure(rel: str, outdir: Path) -> str:
    """Duration of a file we actually hold, in seconds.

    The client's manifest reports length as -1 on every row, so a track that
    downloaded nothing has no duration to report -- there is nothing to measure.
    This is only meaningful for a quarantined or kept file, which is exactly
    where a version mismatch is worth catching by eye.
    """
    path = outdir / rel
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=10)
        val = (out.stdout or "").strip().splitlines()
        return val[0].strip() if val else ""
    except Exception:  # noqa: BLE001
        return ""


def quarantine(track: dict, outdir: Path) -> str:
    """Move a title-only match out of the finished folder so it cannot be
    mistaken for the real track. Returns the new path, or '' if nothing moved.
    """
    dest = outdir / "unverified"
    dest.mkdir(parents=True, exist_ok=True)
    for base in _job_dirs(outdir):
        if base == dest:
            continue
        for p in base.iterdir() if base.is_dir() else []:
            if not p.is_file() or p.suffix.lower() not in AUDIO_SUFFIX:
                continue
            stem = re.sub(r"[^a-z0-9]+", " ", p.stem.lower()).strip()
            want = re.sub(r"[^a-z0-9]+", " ", track["title"].lower()).strip()
            if want and want in stem:
                target = dest / p.name
                try:
                    p.rename(target)
                    return str(target.relative_to(outdir))
                except OSError:
                    return ""
    return ""


def write_failures(tracks: list[dict]) -> Path:
    """One line per track that did not make it. No essay, no diagnosis.

    observed_length_sec is the length the network offered for the last thing
    actually searched. Whether that is the right version is an eyeball job:
    a 7:15 'Donuts' against a 5:55 set rip is a different recording, and no
    automation here can prove which one you meant. The number is here so you
    can do that comparison yourself in one pass.
    """
    missing = [t for t in tracks if not t["done"]]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = HOME / "logs" / f"failures-{stamp}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["artist", "title", "last_artist_query", "last_title_query",
                     "rung", "rounds_attempted", "failurereason",
                     "observed_length_sec", "match_confidence", "quarantined_to"])
        for t in missing:
            a, ti, unverified = t["ladder"][t["rung"]]
            wr.writerow([t["artist"], t["title"], a, ti, t["rung"], t["rounds"],
                         t["reason"], t["observed"],
                         "unverified" if unverified else "artist+title",
                         t.get("quarantined", "")])
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--out", type=Path, default=None,
                    help="output dir (default: downloads/<source stem>)")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--quality", default="320", choices=sorted(QUALITY_COND),
                    help="320 (default), 128, flac, any")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="accept a title-only match as done. Off by default: "
                         "those are frequently a different artist's track.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.source.is_file():
        print(f"fetch: input not found: {args.source}", file=sys.stderr)
        return 2

    # Soulseek allows one connection per account. Two supervisors means two
    # client sessions, which kick each other off with
    # "server kicked this client, probably because the same account logged in
    # elsewhere". Refuse to start a second one.
    lock = HOME / "logs" / "fetch.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.is_file():
        try:
            old = int(lock.read_text().strip())
            os.kill(old, 0)
            print(f"fetch: already running as pid {old}; refusing to start a "
                  f"second session. kill it first if that is wrong.", file=sys.stderr)
            return 4
        except (ValueError, ProcessLookupError, PermissionError):
            lock.unlink(missing_ok=True)
    lock.write_text(str(os.getpid()))

    tracks = load_tracks(args.source)
    if not tracks:
        print("fetch: no song jobs found in input", file=sys.stderr)
        return 3

    outdir = args.out or (HOME / "downloads" / args.source.stem)
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = HOME / "logs" / "fetch"
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"[fetch] {len(tracks)} tracks -> {outdir}")
    total = len(tracks)

    claimed: dict = {}
    for rnd in range(1, args.rounds + 1):
        rows = read_manifest(outdir)
        for t in tracks:
            harvest(t, rows, outdir, args.allow_unverified)
            top = len(t["ladder"]) - 1 if args.allow_unverified else last_safe_rung(t)
            if not t["done"] and t["rung"] < top:
                t["rung"] += 1

        pending = [t for t in tracks if not t["done"]]
        exhausted = [t for t in pending
                     if t["rung"] >= (len(t["ladder"]) - 1
                                      if args.allow_unverified
                                      else last_safe_rung(t))]
        if exhausted and rnd < args.rounds:
            print(f"[fetch] {len(exhausted)} track(s) out of verified rungs; "
                  f"not searching title-only. Use --allow-unverified to try anyway.")

        if not pending:
            print(f"[fetch] round {rnd}: all {total} tracks present and non-empty. done.")
            print(f"[fetch] failure report: {write_failures(tracks)}")
            return 0

        print(f"[fetch] round {rnd}/{args.rounds}: "
              f"{total - len(pending)} done, {len(pending)} pending")
        for t in pending:
            t["rounds"] = rnd

        listfile = workdir / f"{JOB_STEM}.csv"
        write_query_csv(pending, listfile)
        if args.dry_run:
            print(f"[dry-run] would search {len(pending)} queries")
            return 0
        cleared = clear_partials(outdir)
        if cleared:
            print(f"[fetch] cleared {cleared} stale .incomplete file(s)")
        banned = stale_peers(LOG_PATH, pending)
        if banned:
            print(f"[fetch] banning {len(banned)} stale peer(s) for this round: "
                  f"{', '.join(banned[:6])}{' ...' if len(banned) > 6 else ''}")
        run_once(listfile, outdir, args.dry_run, args.quality, banned)

        index = newest_index(outdir)
        moved = flatten_round(outdir, index)
        if moved:
            print(f"[fetch] flattened {moved} file(s) into {outdir}")

        if args.dry_run:
            return 0
        if rnd < args.rounds:
            wait = BACKOFF[min(rnd, len(BACKOFF) - 1)]
            print(f"[fetch] waiting {wait}s before the next round")
            time.sleep(wait)

    rows = read_manifest(outdir)
    for t in tracks:
        harvest(t, rows, outdir, args.allow_unverified, claimed)
    missing = [t for t in tracks if not t["done"]]
    quar = [t for t in missing if t.get("quarantined")]
    if quar:
        print(f"[fetch] {len(quar)} title-only match(es) moved to "
              f"{outdir / 'unverified'}/ - NOT counted as downloaded:")
        for t in quar:
            print(f"[fetch]   {t['artist']} - {t['title']}  -> {t['quarantined']}")
    print(f"[fetch] finished {total - len(missing)}/{total} after {args.rounds} rounds")
    for t in missing:
        a, ti, unverified = t["ladder"][t["rung"]]
        tag = "  [UNVERIFIED: title-only search, may be a different track]" if unverified else ""
        print(f"[fetch]   still missing: {t['artist']} - {t['title']}"
              f"  (last tried: {a!r} / {ti!r}, rung {t['rung']},"
              f" reason {t['reason'] or '?'}){tag}")
    print(f"[fetch] failure report: {write_failures(tracks)}")
    return 1 if missing else 0


def _release():
    lock = HOME / "logs" / "fetch.lock"
    try:
        if lock.is_file() and lock.read_text().strip() == str(os.getpid()):
            lock.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    try:
        code = main()
    finally:
        _release()
    sys.exit(code)
