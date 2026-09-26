# space-seeker

A retry supervisor and status monitor for the
[Soulseek](https://github.com/fiso64/sockseek) client (`sockseek`, AGPL-3.0).

`sockseek` is a good client with one sharp edge: it searches on **title** and
uses the artist only to *rank* results, and it gives up permanently the moment a
search returns nothing. Point it at a list of 70 tracks you want and you get
whatever the network felt like offering that round, plus no second chance.

This wraps it and fixes the three things that actually go wrong:

| Problem | What this does |
| --- | --- |
| A failed track is retried with the *identical* query, five times | A real escalation ladder: exact query, then version-specific, then the other artist, then the longest title form. The artist is never thrown away |
| A search that returns nothing is treated as final | Re-searched with a looser query each round, up to `--rounds` |
| Rounds write into `round01/`, `round02/`… so the same track is stored 5 times | One folder, flattened each round. Already-downloaded files are skipped, not refetched |
| Title-only matches are accepted as success | Refused by default and moved to `unverified/`. A wrong track that looks right is worse than a miss |
| One dead peer burns the whole round on 10 retries | `--max-retries 2`, and stale peers are banned for the next round |

Everything runs offline except the download itself.

---

## Install

```bash
git clone https://github.com/seanskee777/space-seeker
cd space-seeker

# 1. the client. This project does not ship it.
mkdir -p extracted
curl -L -o sockseek.tar.gz \
  https://github.com/fiso64/sockseek/releases/download/v3.0.5/sockseek_3.0.5_linux-x64.tar.gz
tar xzf sockseek.tar.gz -C extracted
chmod +x extracted/sockseek

# 2. your account
cp config/sldl.conf.example config/sldl.conf
$EDITOR config/sldl.conf        # username + password

# 3. offline self-test, no network, no downloads
./run.sh self-test
```

## Use

```bash
# fetch a list of tracks, retrying and loosening until done
scripts/fetch.py lists/my_tracks.csv --rounds 5

# a rare track nobody has at 320: take it at 128 and remaster later
scripts/fetch.py lists/my_tracks.csv --quality 128

# see what it would search, without touching the network
scripts/fetch.py lists/my_tracks.csv --dry-run
```

When it finishes you get **`logs/failures-<timestamp>.csv`**: one line per track
that did not make it, with the last query tried, which rung it reached, the
client's failurereason, and whether the match was artist-verified. No essay.

`observed_length_sec` is only populated for a track we ended up *holding* a
file for, which in practice means a quarantined match. The client's manifest
reports `length` as `-1` on every row, so a track that downloaded nothing has
no duration to report — there is nothing to measure. It is there so you can
catch a version mismatch by eye: a 7:15 "Donuts" and a 5:55 "Donuts" are
different recordings, and nothing here can tell you which one you wanted.

**Known gap:** `--strict-artist` guarantees the *artist* is right. It does not
guarantee the *version* is right. Ask for a remix and the network can hand back
the plain track, and the run counts it as a success. Checking that properly
needs an expected duration per track, which the client does not give us.

### The system monitor

`scripts/monitor.py` is a small colour-coded window in the corner:

```
  done          142   green       file on disk, non-empty
  running         1   Tiffany     downloading right now
  partial        18   yellow      candidate seen, no finished file
  failed         47   red         searched, nothing usable
  unattempted     0   purple      not tried yet
```

```bash
scripts/monitor.py --out DIR --source lists/my_tracks.csv   # small
scripts/monitor.py --out DIR --source lists/my_tracks.csv --big   # with log
```

**Turning it off.** It is opt-in and nothing starts it for you. There is no
daemon, no autostart entry, no service:

```bash
scripts/monitor.py --close        # close the window
pkill -f scripts/monitor.py       # same, from anywhere
sudo rm scripts/monitor.py        # only if you want the file gone
```

If you ever added a line to `~/.config/hypr/autostart.lua` to launch it at
login, delete that line and run `hyprctl reload`.

## Only ever one at a time

Soulseek permits one connection per account. Two sessions kick each other off
with *"server kicked this client, probably because the same account logged in
elsewhere"*. `fetch.py` takes `logs/fetch.lock` and refuses to start if another
one holds it.

## Options worth knowing

```
--rounds N             retry budget, default 5
--quality 320|128|flac|any
--out DIR              output directory
--no-strict-artist     allow matches whose path lacks the artist (see below)
--allow-unverified     permit title-only matches (see below)
--dry-run              print the queries, no network
```

**`--strict-artist` is on by default and should stay that way.** With it off,
`BONES - Airplane Mode` returns Borgore, Flobots, Fizzikx and Envy recordings of
unrelated songs that happen to share a title, and the run reports *success*.
That is how a library silently fills with the wrong tracks. Turn it off only for
a track that is genuinely posted under a different artist name.

**`--allow-unverified` is off by default.** The deepest rung of the ladder
searches the title alone. `--strict-artist` cannot help there, because there is
no artist to require — so anything offered passes. Those results go to
`unverified/` and are *not* counted as downloaded.

## Layout

```
run.sh                    launcher; owns config, output, logs, --quality
bin/sldl                  wrapper around the sockseek binary
src/sldl_parser.py        offline input parser (stdlib, no network)
scripts/fetch.py          the retry supervisor  <- the interesting one
scripts/monitor.py        colour-coded status window
scripts/sources_to_list.py
scripts/self_test.sh      offline test suite
scripts/verify.sh
tests/                    parser unit tests
config/sldl.conf.example  template, no credentials
```

## Licence

**AGPL-3.0.** Not a stylistic choice: this project exists to drive `sockseek`,
which is AGPL-3.0, and a wrapper around an AGPL program is a combined work.
Publishing it as anything looser would be a licence violation.

Practical effect: you may use it, study it, and fork it freely. If you publish a
modified version, AGPL requires you to publish your changes too. It also
guarantees the copyright notice survives, so a fork cannot legally strip the
author's name.

If you would rather have the loose behaviour, switch `LICENSE` to MIT — but then
do not ship the `sockseek` binary in the repository, and document it as an
external dependency.

## Credits

- **[fiso64/sockseek](https://github.com/fiso64/sockseek)** — the client this
  wraps. AGPL-3.0, Copyright (c) its authors. The binary is not redistributed
  here; fetch it from the upstream release.
- Everyone who has put a file on Soulseek. That network is the whole point.
