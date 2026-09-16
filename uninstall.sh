#!/bin/bash
# Remove PirateBox services and hand the Wi-Fi interface back to the system.
# Shared files and data in /srv/piratebox are kept unless you pass --purge.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1

systemctl disable --now piratebox.target piratebox.service piratebox-hostapd.service \
  piratebox-dnsmasq.service piratebox-net.service 2>/dev/null || true
rm -f /etc/systemd/system/piratebox.target /etc/systemd/system/piratebox*.service
systemctl daemon-reload
rm -f /etc/NetworkManager/conf.d/piratebox.conf
[ -f /etc/dhcpcd.conf ] && sed -i '/^# piratebox$/,+1d' /etc/dhcpcd.conf || true
systemctl unmask hostapd.service 2>/dev/null || true
systemctl is-active -q NetworkManager && nmcli general reload conf 2>/dev/null || true
rm -rf /usr/local/lib/piratebox /var/lib/piratebox
if [ $PURGE -eq 1 ]; then
  rm -rf /etc/piratebox /srv/piratebox
  userdel piratebox 2>/dev/null || true
  echo "PirateBox removed, including all shared files."
else
  echo "PirateBox services removed. Files kept in /srv/piratebox, config in /etc/piratebox."
fi
