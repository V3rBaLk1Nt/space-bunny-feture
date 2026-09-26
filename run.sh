#!/usr/bin/env bash
#
# run.sh - root launcher for the /data/soulseek Soulseek 3.0.5 deployment.
#
# Responsibility:
#   * Drive the already-verified Soulseek 3.0.5 client (/data/soulseek/
#     extracted/sockseek) and its wrapper (/data/soulseek/bin/sldl).
#   * Hard-wire this deployment's local configuration:
#       - config:   /data/soulseek/config/sldl.conf
#       - output:   /data/soulseek/downloads/   (ALL downloads)
#       - logs:     /data/soulseek/logs/
#   * Provide --dry-run: a ZERO-network, ZERO-download preview that only
#     parses the requested input locally (Python stdlib, offline).
#
# Scope contract (unchanged from the original approved specification):
#   * Only /data/soulseek/ is read or written.
#   * No password handling on the command line - credentials live ONLY in
#     /data/soulseek/config/sldl.conf.
#   * No Soulseek searches and no media downloads happen unless 'run' is
#     explicitly invoked for that purpose (never during build/tests).
#   * /data/LION/, Ventoy, partitions, filesystems, EFI and bootloader are
#     never touched.
#
# Usage:
#   run.sh --help | help          This help
#   run.sh version                sldl + sockseek versions
#   run.sh self-test              Run the offline parser unit test suite
#   run.sh dry-run <input> [opts] Offline job preview. NO network, NO download.
#   run.sh preview <input>        Client-side offline preview (--print jobs)
#                                 for inputs that need no network extraction
#   run.sh verify                 Full safe build verification suite
#   run.sh run <input> [opts...]  REAL download session (authenticated).
#                                 Only for deliberate use, never during tests.

set -u

RUN_HOME="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
RUN_BIN="$RUN_HOME/bin/sldl"
RUN_PARSE="$RUN_HOME/src/sldl_parser.py"
RUN_CONF="$RUN_HOME/config/sldl.conf"
RUN_OUT="$RUN_HOME/downloads"
RUN_LOG="$RUN_HOME/logs/sldl.log"
# No default account. Set RUN_USER, or put `username` in config/sldl.conf; a
# personal Soulseek handle has no business being hard-coded in a public repo.
RUN_USER="${RUN_USER:-}"

# Namespace shared with bin/sldl / sockseek.
SLDL_USER="${SLDL_USER:-$RUN_USER}"

die() { printf 'run.sh: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[ -x "$RUN_BIN" ]   || die "wrapper not found or not executable: $RUN_BIN"
[ -f "$RUN_PARSE" ] || die "parser not found: $RUN_PARSE"
[ -f "$RUN_CONF" ]  || die "local config not found: $RUN_CONF"
[ -d "$RUN_OUT" ]   || die "downloads dir not found: $RUN_OUT"
[ -d "$RUN_HOME/logs" ] || die "logs dir not found: $RUN_HOME/logs"
have python3 || die "python3 not found"

usage() {
    sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'
}

# is_offline_safe <input> : exit 0 if the input needs no network extraction.
# Uses the same offline parser sldl uses; rejects any job that would require
# a network extraction (youtube/spotify/bandcamp/musicbrainz URLs).
is_offline_safe() {
    local input="$1"
    python3 - "$input" "$RUN_PARSE" <<'PYEOF' || return $?
import os
import sys
input_src, parser_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.dirname(parser_path))
import sldl_parser as p
try:
    result = p.normalize_input(input_src)
except p.ParseError as exc:
    print("preview: cannot classify input for offline preview:", exc)
    sys.exit(3)
except FileNotFoundError:
    print("preview: input not found:", input_src)
    sys.exit(4)
except Exception as exc:  # noqa: BLE001
    print("preview: cannot read input:", exc)
    sys.exit(5)
for job in result.get("jobs", []):
    if job.get("kind") == "Extract":
        print("preview refused: input requires network extraction ->", job.get("url", "?"))
        sys.exit(6)
sys.exit(0)
PYEOF
}

cmd="${1:-}"
case "$cmd" in
    --help|-h|help)
        usage
        ;;
    version)
        exec "$RUN_BIN" version
        ;;
    self-test)
        exec "$RUN_BIN" self-test
        ;;
    dry-run)
        shift
        [ $# -ge 1 ] || die "usage: run.sh dry-run <input> [parser opts...]"
        printf 'run.sh dry-run: local offline parse of: %s\n' "$1"
        printf 'run.sh dry-run: NO network access, NO Soulseek search, NO download.\n'
        # Guaranteed-offline: the stdlib parser, never the network client.
        exec python3 "$RUN_PARSE" --human "$@"
        ;;
    preview)
        shift
        [ $# -ge 1 ] || die "usage: run.sh preview <input>"
        is_offline_safe "$1" || die "preview only supports offline-safe inputs"
        printf 'run.sh preview: client-side offline job preview of: %s\n' "$1"
        exec "$RUN_BIN" run --config "$RUN_CONF" --output-dir "$RUN_OUT" \
            --log-file "$RUN_LOG" --print jobs "$@"
        ;;
    verify)
        exec "$RUN_HOME/scripts/verify.sh"
        ;;
    run)
        shift
        [ $# -ge 1 ] || die "usage: run.sh run <input> [client opts...] (REAL download session)"
        printf 'run.sh run: authenticating and downloading to %s\n' "$RUN_OUT" >&2
        # Pull --quality and --strict-artist out of the caller's flags.
        # A rare track that nobody has at 320 can be retried at 128 and
        # remastered later, so the ceiling is a flag rather than a constant.
        quality=320
        strict=1
        # The client defaults to --max-retries 10. A peer that has gone stale
        # does not come back, so ten attempts against one dead peer burn an
        # entire round and the track still fails. Two is enough to absorb one
        # genuinely flaky transfer.
        retries=2
        rest=()
        while [ $# -gt 0 ]; do
            case "$1" in
                --quality) quality="${2:-320}"; shift 2 ;;
                --quality=*) quality="${1#*=}"; shift ;;
                --strict-artist) strict=1; shift ;;
                --no-strict-artist) strict=0; shift ;;
                --max-retries) retries="${2:-2}"; shift 2 ;;
                --max-retries=*) retries="${1#*=}"; shift ;;
                *) rest+=("$1"); shift ;;
            esac
        done
        case "$quality" in
            320) cond="format=mp3; br=320" ;;
            128) cond="format=mp3; br=128" ;;
            flac) cond="format=flac" ;;
            any)  cond="format=mp3" ;;
            *)    die "--quality must be one of: 320 128 flac any (got '$quality')" ;;
        esac
        case "$retries" in
            ''|*[!0-9]*) die "--max-retries must be a whole number (got '$retries')" ;;
        esac
        # Strict artist matching is ON by default and that is deliberate.
        # The client searches title-first and uses the artist only to rank
        # results, so without this a query for "BONES - Airplane Mode" happily
        # returns Borgore, Flobots and Fizzikx recordings of a track with the
        # same name, and the fetch reports success. Wrong tracks that look
        # right are far worse than a miss. Turn it off with --no-strict-artist
        # if a track is only ever posted under a different artist name.
        strict_args=()
        [ "$strict" -eq 1 ] && strict_args=(--strict-artist --pref-strict-artist)
        # "$@" must come BEFORE --cond. Appending caller flags after the
        # condition string made sldl fold them into the condition parser and
        # abort with "Unknown condition '<word>'". Verified: the same search
        # succeeds when --cond is given last.
        exec "$RUN_BIN" run --config "$RUN_CONF" --output-dir "$RUN_OUT" \
            --log-file "$RUN_LOG" --max-retries "$retries" \
            "${strict_args[@]}" "${rest[@]}" --cond "$cond"
        ;;
    '')
        usage
        exit 2
        ;;
    -d|--dry-run)
        # Support "--dry-run ..." as an alias for the dry-run subcommand.
        shift
        [ $# -ge 1 ] || die "usage: run.sh --dry-run <input> [parser opts...]"
        printf 'run.sh dry-run: local offline parse of: %s\n' "$1"
        printf 'run.sh dry-run: NO network access, NO Soulseek search, NO download.\n'
        exec python3 "$RUN_PARSE" --human "$@"
        ;;
    *)
        die "unknown command '$cmd' (see run.sh --help)"
        ;;
esac