#!/bin/sh
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
umask 077

CORE_PATH=''
API_SOCKET=''
CORE_UID=0
POLL_INTERVAL=0.5
API_TIMEOUT=1
STATE_DIR='/Library/Application Support/killswitch'
RULES="$STATE_DIR/pf.rules"
RULES_TMP="$STATE_DIR/pf.rules.tmp"
ANCHOR='io.github.valdemar-yu.killswitch'
BLOCKED_LABEL='killswitch-blocked'
SCRIPT_DIR=$(CDPATH= cd "$(/usr/bin/dirname "$0")" && /bin/pwd)
CONFIG_FILE="${KILLSWITCH_CONFIG:-$STATE_DIR/config.json}"

# Use the kernel's local process listing for the core. A sleeping or stopped
# core still exists; network reachability is not a process-health signal.
# A zombie has already exited and must not keep the firewall open.
load_config() {
  settings=$(/usr/bin/python3 "$SCRIPT_DIR/lib/config.py" export "$CONFIG_FILE")
  IFS="$(printf '\t')" read -r CORE_PATH API_SOCKET CORE_UID POLL_INTERVAL API_TIMEOUT <<EOF
$settings
EOF
}

get_core_pid() {
  processes=$(/bin/ps -Aww -o pid=,uid=,stat=,comm=) || return 2
  printf '%s\n' "$processes" | /usr/bin/awk -v core="$CORE_PATH" -v expected_uid="$CORE_UID" '
    $2 == expected_uid && $3 !~ /^Z/ {
      candidate = $1
      command = $0
      sub(/^[[:space:]]*[0-9]+[[:space:]]+[0-9]+[[:space:]]+[^[:space:]]+[[:space:]]+/, "", command)
      if (command == core) found = candidate
    }
    END {
      if (found != "") print found
      else exit 1
    }
  '
}

get_tun_state() {
  # Unix sockets do not need Wi-Fi, DNS, a proxy, or an Internet connection.
  # Read the effective TUN setting, including changes made through the UI.
  /usr/bin/curl --unix-socket "$API_SOCKET" --noproxy '*' \
    --connect-timeout "$API_TIMEOUT" --max-time "$API_TIMEOUT" -fsS http://localhost/configs 2>/dev/null | \
    /usr/bin/python3 -c '
import json, re, sys
try:
    config = json.load(sys.stdin)
    tun = config.get("tun")
    if not isinstance(tun, dict):
        raise ValueError
    if tun.get("enable") is False:
        print("disabled -")
    elif tun.get("enable") is True:
        device = tun.get("device")
        if not isinstance(device, str) or not re.fullmatch(r"utun[0-9]+", device):
            raise ValueError
        print("enabled " + device)
    else:
        raise ValueError
except (ValueError, TypeError, AttributeError):
    sys.exit(2)
'
}

evaluate_status() {
  core_pid=''
  tun_if=''
  next_mode=blocked
  reason='core_absent'
  if core_pid=$(get_core_pid); then
    :
  else
    rc=$?
    if [ "$rc" -eq 2 ]; then reason='process_list_unreadable'; fi
    return
  fi

  if tun_state=$(get_tun_state); then
    case "$tun_state" in
      'disabled -') reason='tun_disabled'; return ;;
      'enabled '*) tun_if="${tun_state#enabled }" ;;
      *) reason='tun_state_unreadable'; return ;;
    esac
  else
    reason='tun_state_unreadable'
    return
  fi

  # Existence is independent of UP/RUNNING flags. A network transition may
  # change those flags without removing the virtual interface.
  if /sbin/ifconfig "$tun_if" >/dev/null 2>&1; then
    next_mode=running
    reason='core_and_tun_present'
  else
    reason='tun_interface_missing'
  fi
}

render_rules() {
  case "$1" in
    running)
      # An empty anchor leaves the system's normal rules in control, including
      # DHCP and network recovery. It adds no direct-traffic restrictions.
      printf '%s\n' '# killswitch: core and TUN present; anchor inactive'
      ;;
    blocked)
      printf '%s\n' \
        '# killswitch: core or TUN unavailable' \
        'pass quick on lo0 all no state' \
        'pass out quick inet proto udp from any port 68 to 255.255.255.255 port 67 no state' \
        'pass in quick inet proto udp from any port 67 to any port 68 no state' \
        'pass out quick inet6 proto udp from fe80::/10 port 546 to ff02::1:2 port 547 no state' \
        'pass in quick inet6 proto udp from fe80::/10 port 547 to fe80::/10 port 546 no state' \
        'pass out quick inet6 proto icmp6 from any to { fe80::/10, ff02::/16 } icmp6-type { routersol, neighbrsol, neighbradv } no state' \
        'pass in quick inet6 proto icmp6 from fe80::/10 to any icmp6-type { routeradv, neighbrsol, neighbradv } no state' \
        "block drop quick all label \"$BLOCKED_LABEL\""
      ;;
    *) return 2 ;;
  esac
}

# Read-only checks and rule previews work without installing the daemon.
action=''
if [ "$#" -gt 0 ]; then action=$1; fi
case "$action" in
  --check)
    load_config
    evaluate_status
    printf 'mode=%s core_pid=%s tun_if=%s reason=%s\n' "$next_mode" "$core_pid" "$tun_if" "$reason"
    [ "$next_mode" = running ] && exit 0
    exit 1
    ;;
  --check-core)
    load_config
    if pid=$(get_core_pid); then
      printf 'core_running pid=%s\n' "$pid"
      exit 0
    else
      rc=$?
      printf 'core_absent_or_unreadable\n'
      exit "$rc"
    fi
    ;;
  --rules)
    [ "$#" -eq 2 ] || exit 2
    render_rules "$2"
    exit
    ;;
  '') ;;
  *) printf '%s\n' 'Usage: watchdog.sh [--check | --check-core | --rules running|blocked]' >&2; exit 2 ;;
esac

[ "$(/usr/bin/id -u)" -eq 0 ] || {
  printf '%s\n' 'The watchdog must run as root; --check and --rules are read-only.' >&2
  exit 1
}
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

load_rules() {
  render_rules "$mode" > "$RULES_TMP"
  /bin/mv "$RULES_TMP" "$RULES"
  /sbin/pfctl -a "$ANCHOR" -f "$RULES" >/dev/null
  loaded_rules=$(/sbin/pfctl -a "$ANCHOR" -sr 2>/dev/null)
}

enable_pf() {
  # Own one PF reference, so rollback can release it without disabling PF
  # references held by macOS or another application.
  old_token=''
  if [ -f "$STATE_DIR/pf.token" ]; then
    old_token=$(cat "$STATE_DIR/pf.token")
  fi
  /sbin/pfctl -E > "$STATE_DIR/pf-enable.out" 2>&1
  token=$(/usr/bin/awk '/^Token : [0-9]+$/ {print $3}' "$STATE_DIR/pf-enable.out")
  case "$token" in
    ''|*[!0-9]*) printf '%s\n' 'Cannot read PF enable token.' >&2; return 1 ;;
  esac
  printf '%s\n' "$token" > "$STATE_DIR/pf.token"
  case "$old_token" in
    ''|*[!0-9]*) ;;
    *) /sbin/pfctl -X "$old_token" >/dev/null 2>&1 || true ;;
  esac
}

ensure_pf() {
  main_rules=$(/sbin/pfctl -sr 2>/dev/null)
  case "$main_rules" in
    *"anchor \"$ANCHOR\""*) ;;
    *)
      /usr/bin/python3 "$SCRIPT_DIR/lib/pf_config.py" install /etc/pf.conf \
        "$STATE_DIR/pf.conf.tmp" --seed "$STATE_DIR/blocked.rules"
      /sbin/pfctl -vnf "$STATE_DIR/pf.conf.tmp" >/dev/null
      /bin/cat "$STATE_DIR/pf.conf.tmp" > /etc/pf.conf
      /sbin/pfctl -f /etc/pf.conf >/dev/null
      load_rules
      /sbin/pfctl -F states >/dev/null
      ;;
  esac

  current_rules=$(/sbin/pfctl -a "$ANCHOR" -sr 2>/dev/null)
  if [ "$current_rules" != "$loaded_rules" ]; then
    load_rules
    /sbin/pfctl -F states >/dev/null
  fi

  pf_info=$(/sbin/pfctl -s info 2>/dev/null)
  case "$pf_info" in
    *'Status: Enabled'*) ;;
    *) enable_pf; /sbin/pfctl -F states >/dev/null ;;
  esac
}

# A normal watchdog exit retains a deny-only anchor until launchd restarts it.
# SIGKILL cannot run this trap; launchd's KeepAlive handles that case.
on_exit() {
  trap - EXIT HUP INT TERM
  mode=blocked
  load_rules >/dev/null 2>&1 || true
  /sbin/pfctl -F states >/dev/null 2>&1 || true
}
trap on_exit EXIT
trap 'exit 0' HUP INT TERM

mode=blocked
loaded_rules=''
load_rules
/sbin/pfctl -F states >/dev/null
ensure_pf
load_config
last_mode=''
last_key=''

while :; do
  evaluate_status

  if [ "$next_mode" != "$last_mode" ]; then
    mode=$next_mode
    load_rules
    # Connections created by other PF consumers must not outlive a crash.
    if [ "$mode" = blocked ]; then /sbin/pfctl -F states >/dev/null; fi
    last_mode=$mode
  fi

  key="$next_mode:$reason:$core_pid:$tun_if"
  if [ "$key" != "$last_key" ]; then
    {
      printf 'version=0.1.0\nmode=%s\ncore_pid=%s\ntun_if=%s\nreason=%s\n' "$mode" "$core_pid" "$tun_if" "$reason"
      /bin/date -u '+changed_at=%Y-%m-%dT%H:%M:%SZ'
    } > "$STATE_DIR/status.tmp"
    /bin/mv "$STATE_DIR/status.tmp" "$STATE_DIR/status"
    printf '%s mode=%s core_pid=%s tun_if=%s reason=%s\n' \
      "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" "$mode" "$core_pid" "$tun_if" "$reason"
    last_key=$key
  fi

  ensure_pf
  /bin/sleep "$POLL_INTERVAL"
done
