#!/usr/bin/env bash
# Install the canada-geo-lake timers as *user* units (no root, no system-wide
# side effects). Run with no arguments to see what it would do.
#
#   ./install.sh --install    link units and enable timers
#   ./install.sh --status     show timer state and last runs
#   ./install.sh --remove     disable and unlink
#
# Note: user timers only fire while the user has a session unless lingering is
# enabled:  loginctl enable-linger "$USER"
set -euo pipefail
OPS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNITS="$HOME/.config/systemd/user"
TIMERS=(canada-geo-harvest@tenure.timer canada-geo-harvest@geoscience.timer
        canada-geo-harvest@rasters.timer canada-geo-watchdog.timer)

case "${1:-}" in
  --install)
    mkdir -p "$UNITS"
    for f in canada-geo-harvest@.service canada-geo-harvest@.timer \
             canada-geo-watchdog.service canada-geo-watchdog.timer; do
      ln -sf "$OPS/$f" "$UNITS/$f"
    done
    for d in "$OPS"/*.timer.d; do
      [ -d "$d" ] || continue
      mkdir -p "$UNITS/$(basename "$d")"
      ln -sf "$d"/*.conf "$UNITS/$(basename "$d")/"
    done
    systemctl --user daemon-reload
    for t in "${TIMERS[@]}"; do systemctl --user enable --now "$t"; done
    echo "enabled. lingering: $(loginctl show-user "$USER" -p Linger --value)"
    ;;
  --remove)
    for t in "${TIMERS[@]}"; do systemctl --user disable --now "$t" || true; done
    rm -f "$UNITS"/canada-geo-harvest@.{service,timer} \
          "$UNITS"/canada-geo-watchdog.{service,timer}
    rm -rf "$UNITS"/canada-geo-harvest@*.timer.d
    systemctl --user daemon-reload
    echo "removed"
    ;;
  --status)
    systemctl --user list-timers 'canada-geo-*' --all || true
    "$OPS/../.venv/bin/python" "$OPS/../src/run_class.py" --status
    ;;
  *)
    echo "would link these into $UNITS and enable them:"
    printf '  %s\n' "${TIMERS[@]}"
    echo
    echo "schedules:"
    grep -h OnCalendar "$OPS"/*.timer.d/*.conf "$OPS"/canada-geo-watchdog.timer
    echo
    echo "re-run with --install to apply, --status to inspect, --remove to undo"
    ;;
esac
