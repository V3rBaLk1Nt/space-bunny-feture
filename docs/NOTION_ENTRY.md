# Soulseek downloader - /data/soulseek (summary entry)

> Paste-style notes for tracking this deployment. Contains NO credentials.

## Status
- **Installed:** Soulseek 3.0.5 (Linux x64), verified archive + binary SHA256.
- **Ready:** configuration is in place and the full safe test suite passes.
- **Offline-safe:** parser + dry-run do not touch the network.

## Where things live
- Root launcher: `/data/soulseek/run.sh`
- Client binary: `/data/soulseek/extracted/sockseek` (3.0.5)
- Wrapper: `/data/soulseek/bin/sldl`
- Private config: `/data/soulseek/config/sldl.conf` (mode 600)
- Template: `/data/soulseek/config/sldl.conf.example` (no credentials)
- Downloads: `/data/soulseek/downloads/`
- Logs: `/data/soulseek/logs/sldl.log`
- Source lists: `/data/soulseek/lists/`
- Docs: `/data/soulseek/docs/`

## Account / quality settings (effective)
| Field | Value |
|---|---|
| Username | `YOUR_SOULSEEK_USERNAME` |
| Password | set in `config/sldl.conf` only (never displayed/logged) |
| Output dir | `/data/soulseek/downloads/` |
| Preferred format | `mp3` |
| Preferred min bitrate | `320` kbps |
| Listener | disabled |

## Day-to-day commands
```bash
/data/soulseek/run.sh self-test              # quick offline checks
/data/soulseek/run.sh dry-run lists/music.txt
/data/soulseek/run.sh preview lists/music.txt
/data/soulseek/run.sh verify                 # full suite
/data/soulseek/run.sh run lists/music.txt    # REAL downloads (deliberate only)
```

## Safety contract
- Never passes the password on the command line; `bin/sldl` refuses secret
  flags (`--pass`, spotify/yt tokens).
- `dry-run`/`preview` never search or download.
- Nothing outside `/data/soulseek/` is touched (verified by `verify.sh`).
- No-fly: `/data/LION/`, Ventoy, partitions, filesystems, EFI, bootloader.

## Recorded verification
- Archive SHA256 `d0a1e909...66`, binary SHA256 `ebbf1781...e053` (match).
- `sockseek --version` = `3.0.5`.
- Verdict: `VERIFY OK` - see `docs/INSTALL_REPORT.txt` for full output.

## To start a real download later
Run `/data/soulseek/run.sh run <input>` with the private config present.
Authentication and media transfer happen only in that step.