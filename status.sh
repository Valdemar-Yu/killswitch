#!/bin/sh
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
STATE_DIR='/Library/Application Support/killswitch'
LABEL='io.github.valdemar-yu.killswitch'
[ "$(/usr/bin/id -u)" -eq 0 ] || {
  printf '%s\n' 'Run killswitch status, or run this script with sudo.' >&2
  exit 1
}
job=$(/bin/launchctl print "system/$LABEL" 2>/dev/null || true)
if [ -z "$job" ]; then
  printf '%s\n' 'protection=rolled_back'
else
  printf '%s\n' 'protection=armed'
  pid=$(printf '%s\n' "$job" | /usr/bin/awk '$1 == "pid" && $2 == "=" {print $3}')
  printf 'watchdog_pid=%s\n' "$pid"
  if [ -f "$STATE_DIR/status" ]; then /bin/cat "$STATE_DIR/status"; fi
  printf '%s\n' 'PF anchor rules:'
  /sbin/pfctl -a "$LABEL" -sr
fi
