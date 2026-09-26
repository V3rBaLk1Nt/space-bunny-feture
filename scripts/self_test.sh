#!/usr/bin/env bash
#
# self_test.sh - offline self-test suite for the /data/soulseek deployment.
#
# Everything here is offline and safe:
#   * parser unit tests (stdlib only, no sockets)
#   * parser smoke tests over the sample inputs
#   * dry-run of the built-in source list (goes through the local parser
#     only - never the network client)
#   * downloads directory writability check
#   * password-hygiene check (no real password in the test output)
#
# Run with: /data/soulseek/scripts/self_test.sh   (or: run.sh self-test)
# No Soulseek search, no authentication and no download is performed.

set -u

SL_HOME="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
SL_BIN="$SL_HOME/bin/sldl"
SL_PARSE="$SL_HOME/src/sldl_parser.py"
SL_TESTS="$SL_HOME/tests"
SL_OUT="$SL_HOME/downloads"
SL_LISTS="$SL_HOME/lists"
SL_SAMPLES="$SL_HOME/samples"
SL_RUN="$SL_HOME/run.sh"

pass=0
fail=0
ok()   { printf '  PASS: %s\n' "$*";    pass=$((pass+1)); }
bad()  { printf '  FAIL: %s\n' "$*";    fail=$((fail+1)); }

section() { printf '\n== %s ==\n' "$*"; }

[ -x "$SL_BIN" ] || { echo "sldl wrapper not found"; exit 1; }

section "Parser unit test suite"
python3 -m unittest discover -s "$SL_TESTS" -v 2>&1 | tail -5
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] && ok "parser unit tests ($rc)" || bad "parser unit tests ($rc)"

section "Parser smoke tests over sample inputs"
for sample in "$SL_SAMPLES"/albums.csv "$SL_SAMPLES"/mixed.csv "$SL_SAMPLES"/items.list \
              "$SL_SAMPLES"/albums_notitle.csv "$SL_SAMPLES"/tracks.csv
do
    if output=$(python3 "$SL_PARSE" --human "$sample" 2>&1); then
        ok "parsed $(basename "$sample")"
    else
        bad "parse failed: $(basename "$sample") ($output)"
    fi
done

section "dry-run of built-in source list (offline only)"
printf '%s\n' "--- run.sh dry-run lists/music.txt ---"
"$SL_RUN" dry-run "$SL_LISTS/music.txt"
rc=$?
[ "$rc" -eq 0 ] && ok "dry-run list rc=$rc" || bad "dry-run list rc=$rc"

if output=$("$SL_RUN" dry-run "$SL_SAMPLES/tracks.csv" 2>&1); then
    ok "dry-run tracks.csv"
else
    bad "dry-run tracks.csv"
fi

section "downloads directory writability"
if tfile="$SL_OUT/.self_test_write_probe.$$"; : >"$tfile" 2>/dev/null; then
    if rm -f "$tfile"; then
        ok "downloads dir writable ($SL_OUT)"
    else
        bad "could not remove write probe"
    fi
else
    bad "downloads dir NOT writable ($SL_OUT)"
fi

section "password hygiene in this session's output"
out=$("$SL_RUN" dry-run "$SL_LISTS/music.txt" 2>&1)
needle=$(sed -n 's/^[[:space:]]*password[[:space:]]*=[[:space:]]*//p' "$SL_HOME/config/sldl.conf" | tail -1)
if [ -n "$needle" ] && printf '%s\n' "$out" | grep -q "$needle"; then
    bad "dry-run output leaked the local password value"
else
    ok "no password value in dry-run or self-test output"
fi

printf '\n== summary ==\n'
printf 'PASS: %d  FAIL: %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ] && printf 'SELF-TEST OK (all offline, no network, no download)\n' || printf 'SELF-TEST FAILURES PRESENT\n'
exit "$fail"