#!/bin/sh
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
SCRIPT_DIR=$(CDPATH= cd "$(/usr/bin/dirname "$0")" && /bin/pwd)
DEST='/Library/Application Support/killswitch'
LABEL='io.github.valdemar-yu.killswitch'
PLIST="/Library/LaunchDaemons/$LABEL.plist"
PF='/etc/pf.conf'
[ "$(/usr/bin/id -u)" -eq 0 ] || {
  printf '%s\n' 'Run killswitch rollback, or run this script with sudo.' >&2
  exit 1
}

# Ignore only an already-absent job. A real failure to stop it is an error.
if /bin/launchctl print "system/$LABEL" >/dev/null 2>&1; then
  /bin/launchctl bootout "system/$LABEL"
fi
if /bin/launchctl print "system/$LABEL" >/dev/null 2>&1; then
  printf '%s\n' 'The watchdog is still loaded; rollback failed.' >&2
  exit 1
fi

STAGE=$(/usr/bin/mktemp -d /tmp/killswitch-rollback.XXXXXXXX)
trap '/usr/bin/python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1])" "$STAGE"' EXIT
# Preserve changes made by other PF users after installation.
/usr/bin/python3 "$SCRIPT_DIR/lib/pf_config.py" rollback "$PF" "$STAGE/pf.conf"
/sbin/pfctl -vnf "$STAGE/pf.conf" >/dev/null
/bin/cat "$STAGE/pf.conf" > "$PF"
/sbin/pfctl -a "$LABEL" -F all >/dev/null
/sbin/pfctl -f "$PF" >/dev/null
main_rules=$(/sbin/pfctl -sr 2>/dev/null)
case "$main_rules" in
  *"anchor \"$LABEL\""*) printf '%s\n' 'The PF anchor is still attached; rollback failed.' >&2; exit 1 ;;
esac

if [ -s "$DEST/pf.token" ]; then
  token=$(cat "$DEST/pf.token")
  case "$token" in
    ''|*[!0-9]*) printf '%s\n' 'Invalid saved PF token; rollback failed.' >&2; exit 1 ;;
  esac
  # A stale token can occur after someone disabled PF globally. Check current
  # references before deciding whether this reference still needs releasing.
  references=$(/sbin/pfctl -s References 2>/dev/null)
  if printf '%s\n' "$references" | /usr/bin/awk -v token="$token" '
      {for (i=1;i<=NF;i++) if ("x" $i == "x" token) found=1}
      END {exit !found}
    '; then
    /sbin/pfctl -X "$token" >/dev/null
  fi
fi

/usr/bin/python3 - "$PLIST" "$DEST" <<'PY'
from pathlib import Path
import sys
plist, destination = sys.argv[1:]
Path(plist).unlink(missing_ok=True)
for filename in ("pf.token", "pf-enable.out", "status", "status.tmp", "pf.conf.tmp"):
    (Path(destination) / filename).unlink(missing_ok=True)
PY
printf '%s\n' 'successffully rollback！'
# Keep the root-owned package and configuration for offline re-enabling.
