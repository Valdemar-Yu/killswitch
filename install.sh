#!/bin/sh
# killswitch-managed-install
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
umask 077
SRC=$(CDPATH= cd "$(/usr/bin/dirname "$0")" && /bin/pwd)
DEST='/Library/Application Support/killswitch'
LABEL='io.github.valdemar-yu.killswitch'
PLIST="/Library/LaunchDaemons/$LABEL.plist"
PF='/etc/pf.conf'

[ "$(/usr/bin/uname -s)" = Darwin ] || { printf '%s\n' 'macOS is required.' >&2; exit 1; }
[ "$(/usr/bin/id -u)" -eq 0 ] || {
  printf '%s\n' 'Run sudo /bin/sh install.sh for the first installation.' >&2
  exit 1
}
/usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
  printf '%s\n' '/usr/bin/python3 version 3.9 or newer is required.' >&2
  exit 1
}
for file in install.sh rollback.sh watchdog.sh status.sh bin/killswitch; do /bin/sh -n "$SRC/$file"; done
/usr/bin/plutil -lint "$SRC/launchd.plist" >/dev/null

# Do not silently replace an unrelated package or command.
if [ -d "$DEST" ] && [ -n "$(/bin/ls -A "$DEST")" ]; then
  if [ ! -f "$DEST/package.id" ] || [ "$(cat "$DEST/package.id")" != 'Valdemar-Yu/killswitch' ]; then
    printf '%s\n' 'The destination contains another package; nothing was changed.' >&2
    exit 1
  fi
fi
for command in killswitch clash-enable clash-rollback clash-status; do
  file="/usr/local/bin/$command"
  if [ -e "$file" ] && ! /usr/bin/grep -F 'killswitch-managed-command' "$file" >/dev/null; then
    printf 'Refusing to replace an unrelated command: %s\n' "$file" >&2
    exit 1
  fi
done
if /bin/launchctl print system/com.codex.clash-killswitch >/dev/null 2>&1; then
  printf '%s\n' 'A legacy private kill switch is active. Roll it back before installing this project.' >&2
  exit 1
fi

STAGE=$(/usr/bin/mktemp -d /tmp/killswitch-install.XXXXXXXX)
cleanup() {
  rc=$?
  trap - EXIT
  /usr/bin/python3 - "$STAGE" <<'PY'
import shutil, sys
shutil.rmtree(sys.argv[1])
PY
  if [ "$rc" -ne 0 ]; then
    printf '%s\n' 'Installation failed. To recover, run killswitch rollback.' >&2
    printf 'Source fallback: sudo /bin/sh "%s/rollback.sh"\n' "$SRC" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT

# Validate options before stopping an existing watchdog or changing PF.
# Re-enabling after rollback keeps the selected user's settings.
/usr/bin/python3 "$SRC/lib/config.py" create "$STAGE/config.json" \
  --reuse "$DEST/config.json" "$@"

/bin/launchctl bootout "system/$LABEL" >/dev/null 2>&1 || true
/usr/bin/install -d -o root -g wheel -m 700 "$DEST" "$DEST/lib" "$DEST/bin"
/usr/bin/install -d -o root -g wheel -m 755 /usr/local/bin
if [ "$SRC" != "$DEST" ]; then
  for file in install.sh rollback.sh watchdog.sh status.sh; do
    /usr/bin/install -o root -g wheel -m 755 "$SRC/$file" "$DEST/$file"
  done
  for file in config.py pf_config.py; do
    /usr/bin/install -o root -g wheel -m 644 "$SRC/lib/$file" "$DEST/lib/$file"
  done
  /usr/bin/install -o root -g wheel -m 755 "$SRC/bin/killswitch" "$DEST/bin/killswitch"
  /usr/bin/install -o root -g wheel -m 644 "$SRC/launchd.plist" "$DEST/launchd.plist"
fi
/usr/bin/printf '%s\n' 'Valdemar-Yu/killswitch' > "$DEST/package.id"
/usr/bin/printf '%s\n' '0.1.0' > "$DEST/version"
/usr/bin/install -o root -g wheel -m 600 "$STAGE/config.json" "$DEST/config.json"
for command in killswitch clash-enable clash-rollback clash-status; do
  /usr/bin/install -o root -g wheel -m 755 "$DEST/bin/killswitch" "/usr/local/bin/$command"
done
/usr/bin/install -o root -g wheel -m 644 "$DEST/launchd.plist" "$PLIST"

/bin/sh "$DEST/watchdog.sh" --rules blocked > "$DEST/blocked.rules"
/bin/cp "$DEST/blocked.rules" "$DEST/pf.rules"
if [ ! -f "$DEST/pf.conf.before-install" ]; then
  /bin/cp -p "$PF" "$DEST/pf.conf.before-install"
fi
/usr/bin/python3 "$DEST/lib/pf_config.py" install "$PF" "$DEST/pf.conf.tmp" --seed "$DEST/blocked.rules"
/sbin/pfctl -vnf "$DEST/pf.conf.tmp" >/dev/null
/bin/cat "$DEST/pf.conf.tmp" > "$PF"
/sbin/pfctl -f "$PF" >/dev/null
/sbin/pfctl -a "$LABEL" -f "$DEST/blocked.rules" >/dev/null
/sbin/pfctl -F states >/dev/null

old_token=''
if [ -f "$DEST/pf.token" ]; then old_token=$(cat "$DEST/pf.token"); fi
/sbin/pfctl -E > "$DEST/pf-enable.out" 2>&1
token=$(/usr/bin/awk '/^Token : [0-9]+$/ {print $3}' "$DEST/pf-enable.out")
case "$token" in
  ''|*[!0-9]*) printf '%s\n' 'Cannot read the PF enable token.' >&2; exit 1 ;;
esac
printf '%s\n' "$token" > "$DEST/pf.token"
case "$old_token" in
  ''|*[!0-9]*) ;;
  *) /sbin/pfctl -X "$old_token" >/dev/null 2>&1 || true ;;
esac

/usr/bin/python3 - "$DEST/status" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).unlink(missing_ok=True)
PY
/bin/launchctl bootstrap system "$PLIST"

attempt=0
while [ "$attempt" -lt 20 ]; do
  if [ -s "$DEST/status" ]; then
    job=$(/bin/launchctl print "system/$LABEL" 2>/dev/null || true)
    pid=$(printf '%s\n' "$job" | /usr/bin/awk '$1 == "pid" && $2 == "=" {print $3}')
    case "$pid" in
      ''|*[!0-9]*) ;;
      *)
        if /bin/kill -0 "$pid" 2>/dev/null; then
          info=$(/sbin/pfctl -s info 2>/dev/null)
          main_rules=$(/sbin/pfctl -sr 2>/dev/null)
          case "$info" in *'Status: Enabled'*) ;; *) exit 1 ;; esac
          case "$main_rules" in *"anchor \"$LABEL\""*) ;; *) exit 1 ;; esac
          /bin/cat "$DEST/status"
          printf '%s\n' 'successfully enable！'
          exit 0
        fi
        ;;
    esac
  fi
  attempt=$((attempt + 1))
  /bin/sleep 0.5
done
printf '%s\n' 'Watchdog did not initialize. The blocking rules remain installed.' >&2
exit 1
