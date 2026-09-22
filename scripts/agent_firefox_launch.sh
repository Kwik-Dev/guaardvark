#!/bin/bash
# Launch Firefox in the agent's isolated profile on the virtual display.
# Wired into tint2's Firefox launcher icon via data/agent/desktop/applications/firefox.desktop —
# clicking the bottom-bar Firefox icon runs THIS script, not /usr/share/applications/firefox.desktop
# (which would attach to the user's main browser or open with the default profile, both bad).
#
# Behavior:
#   - If a Firefox process is already running on our profile, raise its window and exit.
#   - Otherwise, clear any stale lock files, launch fresh on :99 with the agent profile.
#
# Race protection: flock on a fixed lockfile so two near-simultaneous launches
# (e.g. auto-launch at display-start vs an early click) can't both grab the
# profile and fight over the lock.

set -u

GUAARDVARK_ROOT="${GUAARDVARK_ROOT:-$(dirname $(dirname $(readlink -f "$0")))}"
DISPLAY_NUM="${GUAARDVARK_AGENT_DISPLAY:-99}"
PROFILE_DIR="$GUAARDVARK_ROOT/data/agent/firefox_profile"
LOCKFILE="/tmp/agent_firefox_launch.lock"

export DISPLAY=":$DISPLAY_NUM"

# Force X11 on Wayland hosts — Mozilla otherwise tries to grab a Wayland
# socket that doesn't exist on the virtual display.
if [ -n "${WAYLAND_DISPLAY:-}" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
    export MOZ_ENABLE_WAYLAND=0
    export GDK_BACKEND=x11
    export WAYLAND_DISPLAY=
    export XDG_SESSION_TYPE=x11
fi

# Serialize check-and-launch so two parallel calls don't both decide to start.
# The browser must not inherit fd 9 (9>&- on the launch lines), or it holds
# the lock for its whole lifetime and every later invocation exits here
# instead of reaching the raise-existing-window branch.
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    # Another invocation is mid-launch; let it finish.
    exit 0
fi

profile_basename="$(basename "$PROFILE_DIR")"

# Already running on our profile? Raise + exit.
if pgrep -f "firefox.*${profile_basename}" > /dev/null 2>&1; then
    if command -v wmctrl &>/dev/null; then
        wmctrl -a "Mozilla Firefox" 2>/dev/null || true
    elif command -v xdotool &>/dev/null; then
        xdotool search --name "Mozilla Firefox" windowactivate 2>/dev/null || true
    fi
    exit 0
fi

# Stale-lock cleanup. Firefox writes .parentlock and lock to the profile dir;
# if a previous instance was killed hard (Xvfb crash, kill -9, OOM) those
# can persist with a dead PID inside and block legitimate restarts. Both lock
# files are absent when Firefox started cleanly, so removing them when
# Firefox is verifiably not running is safe.
for lf in "$PROFILE_DIR/lock" "$PROFILE_DIR/.parentlock"; do
    [ -e "$lf" ] && rm -f "$lf"
done

# CDP (Chrome DevTools Protocol) is opt-in. Google's sign-in flow probes for
# the CDP signature independently of navigator.webdriver and shows "This
# browser or app may not be secure" when it's on — see
# scripts/firefox_user.js.template §ANTI-AUTOMATION-DETECTION. Default off so
# Google/YouTube login works out of the box. The social-outreach DOM scout
# (Discord/Twitter/Facebook) needs CDP; set GUAARDVARK_AGENT_CDP=1 to enable.
CDP_ARGS=()
if [ "${GUAARDVARK_AGENT_CDP:-0}" = "1" ]; then
    CDP_ARGS=(--remote-debugging-port "${GUAARDVARK_AGENT_CDP_PORT:-9222}")
fi

# Launch detached so the wrapper script returns quickly (tint2 doesn't want
# its launcher process held open). nohup + & + redirect, no exec — exec would
# replace the shell with firefox and the trailing & wouldn't apply correctly.
#
# Snap Firefox (Ubuntu's default) needs a different route. Its own launch
# wrapper runs inside the sandbox after our environment and sets
# GDK_BACKEND=wayland and WAYLAND_DISPLAY, so GTK never tries :$DISPLAY_NUM
# and Firefox exits with "cannot open display". Measured 2026-09-21 on the
# firefox 156 snap: the same binary opens on the virtual display once
# GDK_BACKEND=x11 is exported *inside* the sandbox, after the wrapper.
# `snap run --shell` is that hook; $0 carries the display and "$@" the
# browser arguments through to the real binary.
SNAP_FIREFOX="/snap/firefox/current/usr/lib/firefox/firefox"
firefox_on_path="$(command -v firefox 2>/dev/null || true)"
use_snap=false
if [ -x "$SNAP_FIREFOX" ] && command -v snap >/dev/null 2>&1; then
    case "$(readlink -f "$firefox_on_path" 2>/dev/null)" in
        /snap/bin/*|/usr/bin/snap) use_snap=true ;;
        *) grep -qs '/snap/bin/firefox' "$firefox_on_path" && use_snap=true ;;
    esac
fi

if [ "$use_snap" = true ]; then
    # snapd refuses to start an app it cannot place in its own cgroup scope,
    # and it asks the systemd user manager on the session bus to make one.
    # The agent desktop runs on a private bus from dbus-run-session with no
    # systemd behind it, so a click on the desktop icon died with
    # "... is not a snap cgroup for tag snap.firefox.firefox" (2026-09-21).
    # Hand snapd the user's real bus and runtime dir for that step only.
    # Inside the sandbox the browser keeps the agent's bus address instead;
    # the snap cannot reach that socket (private /tmp), so Firefox runs
    # without a session bus and never opens host portals or dialogs.
    snap_env=()
    if [ -S "/run/user/$(id -u)/bus" ]; then
        snap_env=(XDG_RUNTIME_DIR="/run/user/$(id -u)"
                  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus")
    fi
    nohup env "${snap_env[@]}" snap run --shell firefox -c '
        export DISPLAY="$0" GDK_BACKEND=x11 MOZ_ENABLE_WAYLAND=0
        unset WAYLAND_DISPLAY
        if [ -n "$1" ]; then export DBUS_SESSION_BUS_ADDRESS="$1"; else unset DBUS_SESSION_BUS_ADDRESS; fi
        shift
        exec /snap/firefox/current/usr/lib/firefox/firefox "$@"
    ' "$DISPLAY" "${DBUS_SESSION_BUS_ADDRESS:-}" \
        --no-remote \
        "${CDP_ARGS[@]}" \
        --profile "$PROFILE_DIR" \
        >/dev/null 2>&1 9>&- &
else
    nohup firefox \
        --no-remote \
        "${CDP_ARGS[@]}" \
        --profile "$PROFILE_DIR" \
        >/dev/null 2>&1 9>&- &
fi
