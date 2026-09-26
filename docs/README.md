# sldl — Soulseek client wrapper (offline-safe)

`sldl` is the approved launcher for the verified Soulseek v3.0.5 Linux x64
build, isolated under `/data/soulseek/`. It provides an offline input parser,
a passthrough launcher, a unit test suite, and this documentation.

## Scope contract

- **Root only:** anything `sldl` does stays under `/data/soulseek/`. Nothing
  outside it is read or modified.
- **No secrets:** `sldl` never handles passwords or API keys. It refuses
  `--pass`, `--spotify-secret`, `--spotify-token`, `--spotify-refresh`, and
  `--youtube-key`. Credentialed operations must be run by invoking the
  `sockseek` binary directly.
- **No media activity:** `sldl` performs no Soulseek searching, no
  registration, no authentication, and no music/media downloads. The parser is
  purely offline; `run` is a passthrough only.
- **No-fly zones:** `/data/LION/`, Ventoy, partitions, filesystems, EFI, and
  bootloader are never accessed.

## Verified build

| Field            | Value |
|------------------|-------|
| Package URL      | `https://github.com/fiso64/sockseek/releases/download/v3.0.5/sockseek_3.0.5_linux-x64.tar.gz` |
| Expected SHA256  | `d0a1e909297bc4aa0e497bfdd7203884945a78adfb3c0477c8f22830ac951b66` |
| Downloaded SHA256| `d0a1e909297bc4aa0e497bfdd7203884945a78adfb3c0477c8f22830ac951b66` (match) |
| Archive size     | 48,355,957 bytes |
| Archive members  | `LICENSE` (34,523 B) · `sockseek` (111,865,188 B, mode `-rwxr-xr-x`) |
| Path audit       | Both members are relative, flat paths; no absolute or `..` traversal entries |
| Binary SHA256    | `ebbf1781b96a712d23c4a61393d474ebe4507b069c21260ba72c8182c747e053` |
| Binary identity  | `ELF 64-bit LSB pie executable, x86-64, version 1 (SYSV), dynamically linked, interpreter /lib64/ld-linux-x86-64.so.2, GNU/Linux 3.2.0, BuildID[sha1]=6bf9d0cf92926043bd88dd017d6959b5f9795502, stripped` |
| Linker deps      | `libdl.so.2`, `librt.so.1`, `libgcc_s.so.1`, `libpthread.so.0`, `libm.so.6`, `libstdc++.so.6`, `libc.so.6`, `ld-linux-x86-64.so.2` |
| Reported version  | `sockseek --version` → `3.0.5` |
| Extraction       | `tar --no-same-owner --no-same-permissions -xzf ... -C /data/soulseek/extracted` |

All checks were performed with real tool output (`sha256sum`, `tar -tzvf`,
`file`, `readelf`). Never trust a build without verifying these.

## Layout

```
/data/soulseek/
├── sockseek_3.0.5_linux-x64.tar.gz   downloaded + verified archive
├── extracted/
│   ├── LICENSE
│   └── sockseek                       verified ELF binary
├── run.sh                             root launcher (config, output, logs, --dry-run)
├── README.md                          deployment overview
├── bin/sldl                           launcher
├── src/sldl_parser.py                 offline input parser (Python 3, stdlib only)
├── tests/test_parser.py               unittest suite
├── config/
│   ├── sldl.conf                      PRIVATE local config (real credentials, mode 600)
│   └── sldl.conf.example              public template (no credentials)
├── downloads/                         ALL Soulseek downloads land here
├── logs/                              client logs
├── lists/                             source lists (music.txt, sources.list)
├── scripts/
│   ├── sources_to_list.py             CSV -> source list converter
│   ├── self_test.sh                   offline self-test suite
│   └── verify.sh                      full safe verification suite
├── docs/README.md                     this document
├── docs/CHROMEBOOK.md                 Chromebook notes
├── docs/INSTALL_REPORT.txt            installation report
├── docs/NOTION_ENTRY.md               summary entry
└── samples/                           example CSV/list inputs
```

## Usage

    run.sh --help               root launcher help
    run.sh version              sldl + sockseek versions
    run.sh self-test            run the parser unit tests
    run.sh dry-run <input>      OFFLINE parse preview (no network, no download)
    run.sh preview <input>      client-side offline --print jobs (offline-safe inputs only)
    run.sh verify               full safe verification suite
    run.sh run <input>...       REAL download session (authenticated, deliberate)

    sldl --help [topic]      sockseek help (passthrough)
    sldl help input          help on accepted inputs, e.g. `sldl help config`
    sldl parse <input> [opts]
    sldl run <args...>       passthrough to sockseek (defaults user to YOUR_SOULSEEK_USERNAME)
    sldl self-test           run the parser unit tests
    sldl version             launcher + sockseek versions

`parse` options: `--input-type`, `-s/--song`, `--title-col`, `--album-col`,
`--artist-col`, `--length-col`, `--album-track-count-col`, `--json FILE`,
`--human`. Output is normalized JSON (or a human summary).

## Parser semantics

Mirrors the client's documented and empirically confirmed behavior:

- **CSV:** per-row, a non-empty title cell is a Song job; an empty/missing
  title is an Album job. Common column names are auto-detected
  (`Artist`, `Title`, `Album`, `Length`, `Album Track Count`); custom header
  names are mapped with the `--*-col` options. `Length` accepts seconds or
  `h:m:s`/`m:s`.
- **List file:** one item per line — search string, `slsk://` link, CSV path,
  or URL. Lines starting with `#` and blank lines are skipped.
- **Search string:** keyed `key=value` comma-separated properties (`title`,
  `artist`, `album`, `length`, `artist-maybe-wrong`, `album-track-count`), or
  the `ARTIST - TITLE` shorthand (Album job by default, Song job with
  `-s/--song`, exactly as Soulseek's string parser does).
- **slsk:// links:** trailing `/` → Album (folder) job, otherwise a single-file
  Song job.
- **URLs:** classified by host (`youtube`, `spotify`, `bandcamp`,
  `musicbrainz`) as an `Extract` job that would require network extraction —
  never performed here.

The parser is deterministic, offline, and uses only the Python standard
library.

## Tests

Run with:

    sldl self-test
    # or: cd /data/soulseek && python3 -m unittest discover -s tests -v

The suite (23 tests) covers input classification, CSV row semantics (song vs
album), column auto-detection, h:m:s length parsing, keyed/shorthand string
parsing, slsk link classification, URL host classification, list processing,
and error handling.

## Default user

`sldl run` injects `--user YOUR_SOULSEEK_USERNAME` when no `--user` is supplied. Override
per-invocation (`sldl run ... --user NAME`) or permanently via the
`SLDL_USER` environment variable. Credentials are not otherwise handled here:
the account password lives only in `/data/soulseek/config/sldl.conf` and is
used by the client when a real `run.sh run` session is started.