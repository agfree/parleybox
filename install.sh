#!/bin/bash
# PirateBox installer for Debian / Raspberry Pi OS (bookworm or newer).
#
#   sudo ./install.sh [--iface wlan0] [--ssid PirateBox] [--ip 192.168.77.1]
#                     [--channel 6] [--country US] [--no-network]
#
# --no-network installs only the web server (useful on a box whose AP you
# manage some other way, or to serve the portal on an existing LAN).
set -euo pipefail

IFACE=wlan0
SSID="PirateBox - Share Freely"
IP=192.168.77.1
CHANNEL=6
COUNTRY=US
NETWORK=1
LIB=/usr/local/lib/piratebox
ETC=/etc/piratebox
SRV=/srv/piratebox

while [ $# -gt 0 ]; do
  case "$1" in
    --iface) IFACE=$2; shift 2 ;;
    --ssid) SSID=$2; shift 2 ;;
    --ip) IP=$2; shift 2 ;;
    --channel) CHANNEL=$2; shift 2 ;;
    --country) COUNTRY=$2; shift 2 ;;
    --no-network) NETWORK=0; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo ./install.sh)" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)
HOSTNAME_PORTAL=$(sed -n 's/^hostname *= *//p' "$SRC/etc/piratebox.conf" | head -1)
HOSTNAME_PORTAL=${HOSTNAME_PORTAL:-piratebox.lan}
NET=${IP%.*}
DHCP_START=$NET.10
DHCP_END=$NET.250

say() { printf '\033[1;33m>> %s\033[0m\n' "$*"; }

say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
PKGS="python3"
[ $NETWORK -eq 1 ] && PKGS="$PKGS hostapd dnsmasq rfkill iw"
apt-get install -y --no-install-recommends $PKGS

say "Creating user and directories"
id piratebox >/dev/null 2>&1 || useradd --system --home "$SRV" --shell /usr/sbin/nologin piratebox
mkdir -p "$LIB" "$ETC" "$SRV/share/uploads" "$SRV/data"
chown -R piratebox:piratebox "$SRV"
chmod 755 "$SRV" "$SRV/share"

say "Installing web server to $LIB"
rm -rf "$LIB/piratebox"
cp -r "$SRC/piratebox" "$LIB/piratebox"
find "$LIB/piratebox" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
install -m 755 "$SRC/bin/piratebox-net" "$LIB/piratebox-net"
if [ ! -f "$ETC/piratebox.conf" ]; then
  install -m 644 "$SRC/etc/piratebox.conf" "$ETC/piratebox.conf"
else
  say "Keeping existing $ETC/piratebox.conf (new defaults in $ETC/piratebox.conf.new)"
  install -m 644 "$SRC/etc/piratebox.conf" "$ETC/piratebox.conf.new"
fi
install -m 644 "$SRC/etc/piratebox.service" /etc/systemd/system/piratebox.service
install -m 644 "$SRC/etc/piratebox.target" /etc/systemd/system/piratebox.target
if [ ! -e "$SRV/share/README.txt" ]; then
  cat > "$SRV/share/README.txt" <<TXT
This is the shared folder of a PirateBox.
Anything placed here can be downloaded by anyone connected to the Wi-Fi.
Uploads from visitors land in uploads/.
TXT
  chown piratebox:piratebox "$SRV/share/README.txt"
fi

if [ $NETWORK -eq 1 ]; then
  say "Rendering network config for $IFACE ($SSID @ $IP, ch $CHANNEL, $COUNTRY)"
  render() {
    sed -e "s|@IFACE@|$IFACE|g" -e "s|@SSID@|$SSID|g" -e "s|@IP@|$IP|g" \
        -e "s|@CHANNEL@|$CHANNEL|g" -e "s|@COUNTRY@|$COUNTRY|g" \
        -e "s|@HOSTNAME@|$HOSTNAME_PORTAL|g" \
        -e "s|@DHCP_START@|$DHCP_START|g" -e "s|@DHCP_END@|$DHCP_END|g" "$1" > "$2"
  }
  render "$SRC/etc/hostapd.conf.tmpl" "$ETC/hostapd.conf"
  render "$SRC/etc/dnsmasq.conf.tmpl" "$ETC/dnsmasq.conf"
  render "$SRC/etc/network.env.tmpl" "$ETC/network.env"
  for u in piratebox-net piratebox-hostapd piratebox-dnsmasq; do
    sed "s|wlan0|$IFACE|g" "$SRC/etc/$u.service" > "/etc/systemd/system/$u.service"
  done
  mkdir -p /var/lib/piratebox

  say "Handing $IFACE over to PirateBox"
  # NetworkManager (Raspberry Pi OS bookworm+): stop managing the interface
  if [ -d /etc/NetworkManager ]; then
    mkdir -p /etc/NetworkManager/conf.d
    printf '[keyfile]\nunmanaged-devices=interface-name:%s\n' "$IFACE" > /etc/NetworkManager/conf.d/piratebox.conf
    systemctl is-active -q NetworkManager && nmcli general reload conf 2>/dev/null || true
    systemctl is-active -q NetworkManager && nmcli device set "$IFACE" managed no 2>/dev/null || true
  fi
  # dhcpcd (older Raspberry Pi OS)
  if [ -f /etc/dhcpcd.conf ] && ! grep -q "denyinterfaces $IFACE" /etc/dhcpcd.conf; then
    printf '\n# piratebox\ndenyinterfaces %s\n' "$IFACE" >> /etc/dhcpcd.conf
    systemctl is-active -q dhcpcd && systemctl restart dhcpcd || true
  fi
  # the stock units would fight ours for the same daemons/ports
  systemctl disable --now hostapd.service 2>/dev/null || true
  systemctl mask hostapd.service 2>/dev/null || true
  systemctl disable --now dnsmasq.service 2>/dev/null || true
  systemctl disable --now "wpa_supplicant@$IFACE.service" 2>/dev/null || true
  systemctl unmask piratebox-hostapd.service piratebox-dnsmasq.service 2>/dev/null || true
  # regulatory domain so the radio is allowed to transmit
  command -v iw >/dev/null && iw reg set "$COUNTRY" 2>/dev/null || true
  if [ -f /etc/default/crda ]; then sed -i "s/^REGDOMAIN=.*/REGDOMAIN=$COUNTRY/" /etc/default/crda; fi
fi

say "Enabling services"
systemctl daemon-reload
systemctl enable piratebox.target
if [ $NETWORK -eq 1 ]; then
  systemctl enable piratebox-net.service piratebox-hostapd.service piratebox-dnsmasq.service
fi
systemctl enable piratebox.service
systemctl restart piratebox.target

sleep 2
say "Status"
systemctl --no-pager --lines=0 status piratebox.service piratebox-hostapd.service piratebox-dnsmasq.service 2>/dev/null | grep -E 'service|Active' || true
echo
say "Done."
echo "  Shared files:  $SRV/share   (uploads in $SRV/share/uploads)"
echo "  Config:        $ETC/piratebox.conf"
echo "  Logs:          journalctl -u piratebox -u piratebox-hostapd -u piratebox-dnsmasq -f"
if [ $NETWORK -eq 1 ]; then
  echo "  Wi-Fi:         '$SSID' (open)  ->  http://$HOSTNAME_PORTAL/  or  http://$IP/"
  echo
  echo "  NOTE: $IFACE is now dedicated to the access point. If this was your only"
  echo "  network link, use Ethernet (or a second adapter) to manage the box."
fi
