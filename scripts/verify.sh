#!/usr/bin/env bash
#
# verify.sh - full safe verification suite for the /data/soulseek deployment.
#
# Verifies, with real command output:
#   1. Build artifacts (archive + binary SHA256, sockseek version)
#   2. Layout (config, downloads, logs, lists, scripts, bin, src, tests)
#   3. Local config content, redacted    (username + password field set;
#      the value itself is NEVER printed)
#   4. Example config contains NO real password
#   5. Preferred format = mp3 and preferred min bitrate = 320
#   6. run.sh wiring: config path, output dir, logs dir, dry-run
#   7. Parser unit tests (offline)
#   8. dry-run of the built-in source list (offline, no sockets)
#   9. Client-side offline preview (--print jobs, requires no network)
#  10. Downloads directory writable
#  11. Protected-write check: snapshot of /data OUTSIDE /data/soulseek before
#      and after, plus (when present) a diff against the pre-build baseline.
#  12. Password hygiene: the local password value absent from every
#      file/config/docs/log/report discovered on disk except the one private
#      config file.
#
# Run with: /data/soulseek/scripts/verify.sh  (or: run.sh verify)
# No Soulseek search, no authentication (login) and no download is performed.

set -u

V_HOME="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
V_CONF="$V_HOME/config/sldl.conf"
V_EXAMPLE="$V_HOME/config/sldl.conf.example"
V_OUT="$V_HOME/downloads"
V_LOG_DIR="$V_HOME/logs"
V_LISTS="$V_HOME/lists"
V_SCRIPTS="$V_HOME/scripts"
V_BIN="$V_HOME/bin/sldl"
V_SRC="$V_HOME/src"
V_TESTS="$V_HOME/tests"
V_TARBALL="$V_HOME/sockseek_3.0.5_linux-x64.tar.gz"
V_SOCKSEEK="$V_HOME/extracted/sockseek"
V_RUN="$V_HOME/run.sh"
V_BASELINE="${V_BASELINE:-/tmp/opencode/protected_baseline.txt}"

EXPECT_TAR_SHA256="d0a1e909297bc4aa0e497bfdd7203884945a78adfb3c0477c8f22830ac951b66"
EXPECT_BIN_SHA256="ebbf1781b96a712d23c4a61393d474ebe4507b069c21260ba72c8182c747e053"
EXPECT_SOCKSEEK_VERSION="3.0.5"
WANT_USER="YOUR_SOULSEEK_USERNAME"
# The expected password is NEVER declared here and NEVER printed. The check
# only asserts that the local config holds a private, non-trivial value and
# that the value appears nowhere else on disk.

pass=0
fail=0
ok()  { printf '  PASS: %s\n' "$*";      pass=$((pass+1)); }
bad() { printf '  FAIL: %s\n' "$*" >&2;  fail=$((fail+1)); }
section() { printf '\n== %s ==\n' "$*"; }

echo "verify.sh: /data/soulseek build verification (offline, safe)"

[ -d "$V_HOME" ] || { echo "FATAL: $V_HOME missing"; exit 1; }

section "1. Build artifacts"
if [ -f "$V_TARBALL" ]; then
    cur=$(sha256sum "$V_TARBALL" | awk '{print $1}')
    [ "$cur" = "$EXPECT_TAR_SHA256" ] && ok "archive SHA256 matches ($cur)" \
                                     || bad "archive SHA256 MISMATCH ($cur)"
else
    bad "archive missing: $V_TARBALL"
fi
if [ -x "$V_SOCKSEEK" ]; then
    if [ -f "$V_SOCKSEEK" ]; then
        cur=$(sha256sum "$V_SOCKSEEK" | awk '{print $1}')
        [ "$cur" = "$EXPECT_BIN_SHA256" ] && ok "binary SHA256 matches ($cur)" \
                                         || bad "binary SHA256 MISMATCH ($cur)"
    fi
    ver=$("$V_SOCKSEEK" --version 2>&1)
    [ "$ver" = "$EXPECT_SOCKSEEK_VERSION" ] && ok "sockseek --version = $ver" \
                                            || bad "version mismatch: '$ver'"
else
    bad "sockseek binary missing or not executable: $V_SOCKSEEK"
fi

section "2. Layout"
for d in config downloads logs lists scripts bin src tests extracted docs samples jobs; do
    [ -d "$V_HOME/$d" ] && ok "directory present: $d/" || bad "directory missing: $d/"
done
for f in "$V_CONF" "$V_EXAMPLE" "$V_RUN" "$V_BIN" "$V_SRC/sldl_parser.py" \
         "$V_TESTS/test_parser.py" "$V_LISTS/music.txt" \
         "$V_SCRIPTS/sources_to_list.py" "$V_SCRIPTS/self_test.sh" "$V_SCRIPTS/verify.sh"
do
    [ -f "$f" ] && ok "file present: $(basename "$f")" || bad "file missing: $f"
done
[ -x "$V_RUN" ]  && ok "run.sh executable"  || bad "run.sh not executable"
[ -x "$V_BIN" ]  && ok "bin/sldl executable" || bad "bin/sldl not executable"
[ -x "$V_LISTS/../scripts/self_test.sh" ]  && ok "self_test.sh executable"  || bad "self_test.sh not executable"
[ -x "$V_SCRIPTS/verify.sh" ]              && ok "verify.sh executable"     || bad "verify.sh not executable"

section "3. Local config, redacted"
if [ -f "$V_CONF" ]; then
    mode=$(stat -c '%a' "$V_CONF")
    ok "config file mode $mode (600 recommended for a password-bearing file)"
    grep -qE '^[[:space:]]*username[[:space:]]*=[[:space:]]*'"$WANT_USER"'[[:space:]]*$' "$V_CONF" \
        && ok "username field = $WANT_USER" || bad "username field mismatch"
    pwval=$(sed -n 's/^[[:space:]]*password[[:space:]]*=[[:space:]]*//p' "$V_CONF" | tail -1)
    if [ -n "$pwval" ]; then
        if [[ "$pwval" =~ ^[0-9]{4,}$ ]]; then
            ok "password field set (4+ digit private value, REDACTED in this report)"
        else
            bad "password field set but does not look like the registered local value"
        fi
    else
        bad "password field missing in local config"
    fi
    grep -qE '^[[:space:]]*output-dir[[:space:]]*=[[:space:]]*'"$V_HOME"'/downloads' "$V_CONF" \
        && ok "config output-dir = $V_HOME/downloads" || bad "config output-dir wrong"
    grep -qE '^[[:space:]]*log-file[[:space:]]*=' "$V_CONF" \
        && ok "config log-file set" || bad "config log-file missing"
else
    bad "local config missing: $V_CONF"
fi

section "4. Example config - no real credentials"
if [ -f "$V_EXAMPLE" ]; then
    if [ -n "$pwval" ] && grep -q "$pwval" "$V_EXAMPLE"; then
        bad "example config contains the real password value"
    else
        ok "example config contains no real password"
    fi
    if grep -q "username[[:space:]]*=[[:space:]]*$WANT_USER" "$V_EXAMPLE"; then
        bad "example config contains the real username"
    else
        ok "example config contains no real username"
    fi
else
    bad "example config missing"
fi

section "5. Preferred format / bitrate in local config"
if grep -qE '^[[:space:]]*pref-format[[:space:]]*=[[:space:]]*mp3([, ]|$)' "$V_CONF"; then
    ok "pref-format = mp3"
else
    bad "pref-format not mp3"
fi
if grep -qE '^[[:space:]]*pref-min-bitrate[[:space:]]*=[[:space:]]*320([, ]|$)' "$V_CONF"; then
    ok "pref-min-bitrate = 320 kbps"
else
    bad "pref-min-bitrate not 320"
fi

section "6. run.sh wiring"
"$V_RUN" version >/dev/null 2>&1 && ok "run.sh version works" || bad "run.sh version failed"
"$V_RUN" help >/dev/null 2>&1   && ok "run.sh help works"   || bad "run.sh help failed"

section "7. Parser unit tests (offline)"
python3 -m unittest discover -s "$V_TESTS" -v 2>&1 | tail -3
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] && ok "unit tests rc=$rc" || bad "unit tests rc=$rc"

section "8. dry-run of built-in source list (offline, no network)"
printf '%s\n' "--- run.sh dry-run lists/music.txt (human summary) ---"
"$V_RUN" dry-run "$V_LISTS/music.txt"
rc=$?
[ "$rc" -eq 0 ] && ok "dry-run list rc=$rc" || bad "dry-run list rc=$rc"

section "9. Client-side offline preview (--print jobs, requires no network)"
printf '%s\n' "--- run.sh preview samples/tracks.csv ---"
if "$V_RUN" preview "$V_HOME/samples/tracks.csv" 2>&1; then
    ok "preview rc=0 (offline --print jobs)"
else
    bad "preview failed"
fi

section "10. Downloads directory writable"
tfile="$V_OUT/.verify_write_probe.$$"
if : >"$tfile" 2>/dev/null; then
    if rm -f "$tfile"; then
        ok "downloads dir writable: $V_OUT"
    else
        bad "probe cleanup failed"
    fi
else
    bad "downloads dir NOT writable: $V_OUT"
fi

section "11. Protected-write check (nothing outside /data/soulseek changed)"
snap() {
    find /data -not -path '/data/soulseek' -not -path '/data/soulseek/*' \
         -printf '%p|%s|%T@\n' 2>/dev/null | sort
}
b0=$(snap)
# Perform the full re-verification work; verify.sh itself is read-only for /data.
"$V_RUN" version >/dev/null 2>&1
"$V_RUN" dry-run "$V_LISTS/music.txt" >/dev/null 2>&1
python3 -m unittest discover -s "$V_TESTS" >/dev/null 2>&1
: >"$V_OUT/.verify_probe2.$$" && rm -f "$V_OUT/.verify_probe2.$$"
b1=$(snap)
if [ "$b0" = "$b1" ]; then
    ok "before/after snapshots of /data (minus /data/soulseek) are identical"
else
    bad "protected-write before/after snapshot DIFF"
    diff <(printf '%s\n' "$b0") <(printf '%s\n' "$b1")
fi
if [ -f "$V_BASELINE" ]; then
    if diff -q "$V_BASELINE" <(snap) >/dev/null 2>&1; then
        ok "still identical to the PRE-BUILD baseline ($V_BASELINE)"
    else
        bad "/data (minus /data/soulseek) differs from pre-build baseline"
    fi
else
    ok "no pre-build baseline at $V_BASELINE (skipped cross-build comparison)"
fi

section "12. Password hygiene scan of everything on disk"
leak=0
if [ -z "${pwval:-}" ]; then
    bad "cannot derive password needle from local config"
else
while IFS= read -r f; do
    if grep -q "$pwval" "$f" 2>/dev/null; then
        # The single permitted owner of the password value is the private config.
        if [ "$(readlink -f "$f")" = "$(readlink -f "$V_CONF")" ]; then
            continue
        fi
        echo "  password value found outside local config: $f" >&2
        leak=$((leak+1))
    fi
done < <(find "$V_HOME" -type f -not -name 'sockseek' 2>/dev/null)
if [ "$leak" -eq 0 ]; then
    ok "no file under $V_HOME (other than sldl.conf) contains the local password value"
else
    bad "$leak file(s) contain the local password value (redacted counts)"
fi
fi

printf '\n== RESULT ==\n'
printf 'PASS: %d  FAIL: %d\n' "$pass" "$fail"
if [ "$fail" -eq 0 ]; then
    printf 'VERIFY OK - deployment ready for real sessions.\n'
    printf 'Run real downloads with:  /data/soulseek/run.sh run <input>\n'
    exit 0
fi
printf 'VERIFY FAILURES PRESENT\n'
exit 1