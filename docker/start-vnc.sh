#!/usr/bin/env bash
set -euo pipefail

DISPLAY_VALUE="${DISPLAY:-:99}"
XVFB_WHD="${XVFB_WHD:-1920x1080x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"

mkdir -p /tmp/.X11-unix /var/log/duanjv
rm -f /tmp/.X99-lock

Xvfb "${DISPLAY_VALUE}" -screen 0 "${XVFB_WHD}" -ac +extension RANDR > /var/log/duanjv/xvfb.log 2>&1 &
sleep 1

fluxbox > /var/log/duanjv/fluxbox.log 2>&1 &
x11vnc \
  -display "${DISPLAY_VALUE}" \
  -forever \
  -shared \
  -nopw \
  -rfbport "${VNC_PORT}" \
  -listen 0.0.0.0 > /var/log/duanjv/x11vnc.log 2>&1 &

/usr/share/novnc/utils/novnc_proxy \
  --vnc "127.0.0.1:${VNC_PORT}" \
  --listen "${NOVNC_PORT}" \
  --web /usr/share/novnc > /var/log/duanjv/novnc.log 2>&1 &

echo "duanjv desktop is ready."
echo "Open: http://<server-ip>:${NOVNC_PORT}/vnc.html?autoconnect=1&resize=scale"

exec tail -f /dev/null
