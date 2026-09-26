# Soulseek downloader on a Chromebook (ChromeOS / Crostini)

How this `/data/soulseek` deployment behaves when the enclosing Linux
environment is ChromeOS's Linux container (Crostini / "Linux development
environment").

## Environment notes

- Crostini is an LXC/VM-backed Debian container. Everything in this
  deployment lives EXACTLY under `/data/soulseek/` inside that container's
  filesystem.
- `core_pattern` and systemd-behaviour differ from a bare-metal box: if you
  ever need crash diagnostics, note this deployment does NOT install system
  services and does not write outside `/data/soulseek/`.
- No root access is required. All commands below run as the normal user.

## Getting the client running

```
/data/soulseek/run.sh version             # launcher + sockseek 3.0.5
/data/soulseek/run.sh self-test           # offline parser tests
/data/soulseek/run.sh dry-run lists/music.txt
/data/soulseek/run.sh preview lists/music.txt
```

The first real session (only when you intend to download):

```
/data/soulseek/run.sh run lists/music.txt
```

Downloads land in `/data/soulseek/downloads/` regardless of where the shell
started; logs accumulate in `/data/soulseek/logs/sldl.log`.

## Keeping downloads on the mounted /data

The container sees `/data` as a regular mount. Verification in
`scripts/verify.sh` confirms `/data/soulseek/downloads` is writable and that
nothing outside `/data/soulseek/` was modified during the build/tests. On a
Chromebook, back up the whole `/data/soulseek` tree if you want your config,
lists, and download history to survive container rebuilds.

## Password hygiene

The password exists in ONE file and one file only:
`/data/soulseek/config/sldl.conf` (permission `600`). If you export this
deployment to another machine, carry that file privately; never paste its
contents into a shared doc, chat, or example config.

## Minor ChromeOS specifics

- The container keeps running while the ChromeOS session is alive; relaunch
  the Linux app to restart it after a logout.
- If the container is rebuilt, re-create `/data/soulseek` and re-run
  `scripts/verify.sh` before a real session.
- Long downloads: keep the Linux container awake (disable "Sleep when idle"
  for Linux in ChromeOS settings) or use a terminal with `--no-progress`
  and `/data/soulseek/bin/sldl run ...` under `tmux`/`nohup`.