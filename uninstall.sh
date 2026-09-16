#!/bin/bash
# Remove ParleyBox services and hand the Wi-Fi interface back to the system.
# Shared files and data in /srv/parleybox are kept unless you pass --purge.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1

systemctl disable --now parleybox.target parleybox.service parleybox-hostapd.service \
  parleybox-dnsmasq.service parleybox-net.service 2>/dev/null || true
rm -f /etc/systemd/system/parleybox.target /etc/systemd/system/parleybox*.service
systemctl daemon-reload
rm -f /etc/NetworkManager/conf.d/parleybox.conf
[ -f /etc/dhcpcd.conf ] && sed -i '/^# parleybox$/,+1d' /etc/dhcpcd.conf || true
systemctl unmask hostapd.service 2>/dev/null || true
systemctl is-active -q NetworkManager && nmcli general reload conf 2>/dev/null || true
rm -rf /usr/local/lib/parleybox /var/lib/parleybox
if [ $PURGE -eq 1 ]; then
  rm -rf /etc/parleybox /srv/parleybox
  userdel parleybox 2>/dev/null || true
  echo "ParleyBox removed, including all shared files."
else
  echo "ParleyBox services removed. Files kept in /srv/parleybox, config in /etc/parleybox."
fi
