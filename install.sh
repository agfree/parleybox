#!/bin/bash
# ParleyBox installer for Debian / Raspberry Pi OS (bookworm or newer).
#
#   sudo ./install.sh [--iface wlan0] [--ssid ParleyBox] [--ip 192.168.77.1]
#                     [--channel 6] [--country US] [--no-network]
#   sudo ./install.sh --upgrade
#
# --no-network installs only the web server (useful on a box whose AP you
# manage some other way, or to serve the portal on an existing LAN).
# --upgrade installs this copy over an existing install, keeping its Wi-Fi
# settings, config, files, chat and board. Only the web server restarts.
set -euo pipefail

IFACE=wlan0
SSID="ParleyBox - Share Freely"
IP=192.168.77.1
CHANNEL=6
COUNTRY=US
NETWORK=1
UPGRADE=0
NET_OPTS=0
LIB=/usr/local/lib/parleybox
ETC=/etc/parleybox
SRV=/srv/parleybox

while [ $# -gt 0 ]; do
  case "$1" in
    --iface) IFACE=$2; NET_OPTS=1; shift 2 ;;
    --ssid) SSID=$2; NET_OPTS=1; shift 2 ;;
    --ip) IP=$2; NET_OPTS=1; shift 2 ;;
    --channel) CHANNEL=$2; NET_OPTS=1; shift 2 ;;
    --country) COUNTRY=$2; NET_OPTS=1; shift 2 ;;
    --no-network) NETWORK=0; NET_OPTS=1; shift ;;
    --upgrade) UPGRADE=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo ./install.sh)" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)

# Installed revision, for "upgraded X -> Y". git refuses a repo owned by
# another user unless told it is safe, and we run as root. No --dirty: it
# rewrites .git/index, which would leave it owned by root.
version_of() { sed -n 's/^__version__ = "\(.*\)"/\1/p' "$1/parleybox/__init__.py"; }
revision() {
  git -c safe.directory="$SRC" -C "$SRC" describe --tags --always 2>/dev/null || version_of "$SRC"
}

if [ $UPGRADE -eq 1 ]; then
  [ $NET_OPTS -eq 0 ] || { echo "--upgrade keeps the existing Wi-Fi settings; drop the other options" >&2; exit 2; }
  [ -f "$ETC/parleybox.conf" ] && [ -d "$LIB/parleybox" ] ||
    { echo "no ParleyBox install found; run without --upgrade" >&2; exit 1; }
  OLD_REV=$(cat "$LIB/REVISION" 2>/dev/null || version_of "$LIB")
  if [ -f "$ETC/network.env" ]; then
    IFACE=$(sed -n 's/^IFACE=//p' "$ETC/network.env")
    [ -n "$IFACE" ] || { echo "can't read IFACE from $ETC/network.env" >&2; exit 1; }
  else
    NETWORK=0
  fi
fi
HOSTNAME_PORTAL=$(sed -n 's/^hostname *= *//p' "$SRC/etc/parleybox.conf" | head -1)
HOSTNAME_PORTAL=${HOSTNAME_PORTAL:-parleybox.lan}
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
id parleybox >/dev/null 2>&1 || useradd --system --home "$SRV" --shell /usr/sbin/nologin parleybox
mkdir -p "$LIB" "$ETC" "$SRV/share/uploads" "$SRV/data"
chown -R parleybox:parleybox "$SRV"
chmod 755 "$SRV" "$SRV/share"

say "Installing web server to $LIB"
rm -rf "$LIB/parleybox"
cp -r "$SRC/parleybox" "$LIB/parleybox"
find "$LIB/parleybox" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
install -m 755 "$SRC/bin/parleybox-net" "$LIB/parleybox-net"
install -m 755 "$SRC/bin/parleybox-ssh" "$LIB/parleybox-ssh"
revision > "$LIB/REVISION"
if [ ! -f "$ETC/parleybox.conf" ]; then
  install -m 644 "$SRC/etc/parleybox.conf" "$ETC/parleybox.conf"
else
  say "Keeping existing $ETC/parleybox.conf (new defaults in $ETC/parleybox.conf.new)"
  install -m 644 "$SRC/etc/parleybox.conf" "$ETC/parleybox.conf.new"
fi
install -m 644 "$SRC/etc/parleybox.service" /etc/systemd/system/parleybox.service
install -m 644 "$SRC/etc/parleybox.target" /etc/systemd/system/parleybox.target
install -m 644 "$SRC/etc/parleybox-ssh.path" /etc/systemd/system/parleybox-ssh.path
install -m 644 "$SRC/etc/parleybox-ssh.service" /etc/systemd/system/parleybox-ssh.service
if [ ! -e "$SRV/share/README.txt" ]; then
  cat > "$SRV/share/README.txt" <<TXT
This is the cargo hold of a ParleyBox.
Anything placed here can be downloaded by anyone connected to the Wi-Fi.
Cargo brought aboard by visitors lands in uploads/.
TXT
  chown parleybox:parleybox "$SRV/share/README.txt"
fi

# unit files as they were, so an upgrade can tell which ones changed
net_units() { { cat /etc/systemd/system/parleybox-{net,hostapd,dnsmasq}.service 2>/dev/null || true; } | md5sum; }
OLD_UNITS=$(net_units)

if [ $NETWORK -eq 1 ] && [ $UPGRADE -eq 1 ]; then
  say "Keeping Wi-Fi settings in $ETC; updating units for $IFACE"
  for u in parleybox-net parleybox-hostapd parleybox-dnsmasq; do
    sed "s|wlan0|$IFACE|g" "$SRC/etc/$u.service" > "/etc/systemd/system/$u.service"
  done
elif [ $NETWORK -eq 1 ]; then
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
  for u in parleybox-net parleybox-hostapd parleybox-dnsmasq; do
    sed "s|wlan0|$IFACE|g" "$SRC/etc/$u.service" > "/etc/systemd/system/$u.service"
  done
  mkdir -p /var/lib/parleybox

  say "Handing $IFACE over to ParleyBox"
  # NetworkManager (Raspberry Pi OS bookworm+): stop managing the interface
  if [ -d /etc/NetworkManager ]; then
    mkdir -p /etc/NetworkManager/conf.d
    printf '[keyfile]\nunmanaged-devices=interface-name:%s\n' "$IFACE" > /etc/NetworkManager/conf.d/parleybox.conf
    systemctl is-active -q NetworkManager && nmcli general reload conf 2>/dev/null || true
    systemctl is-active -q NetworkManager && nmcli device set "$IFACE" managed no 2>/dev/null || true
  fi
  # dhcpcd (older Raspberry Pi OS)
  if [ -f /etc/dhcpcd.conf ] && ! grep -q "denyinterfaces $IFACE" /etc/dhcpcd.conf; then
    printf '\n# parleybox\ndenyinterfaces %s\n' "$IFACE" >> /etc/dhcpcd.conf
    systemctl is-active -q dhcpcd && systemctl restart dhcpcd || true
  fi
  # the stock units would fight ours for the same daemons/ports
  systemctl disable --now hostapd.service 2>/dev/null || true
  systemctl mask hostapd.service 2>/dev/null || true
  systemctl disable --now dnsmasq.service 2>/dev/null || true
  systemctl disable --now "wpa_supplicant@$IFACE.service" 2>/dev/null || true
  systemctl unmask parleybox-hostapd.service parleybox-dnsmasq.service 2>/dev/null || true
  # regulatory domain so the radio is allowed to transmit
  command -v iw >/dev/null && iw reg set "$COUNTRY" 2>/dev/null || true
  if [ -f /etc/default/crda ]; then sed -i "s/^REGDOMAIN=.*/REGDOMAIN=$COUNTRY/" /etc/default/crda; fi
fi

say "Enabling services"
systemctl daemon-reload
systemctl enable parleybox.target
if [ $NETWORK -eq 1 ]; then
  systemctl enable parleybox-net.service parleybox-hostapd.service parleybox-dnsmasq.service
fi
systemctl enable parleybox.service parleybox-ssh.path
if [ $UPGRADE -eq 0 ]; then
  systemctl restart parleybox.target
elif [ "$(net_units)" != "$OLD_UNITS" ]; then
  say "Network units changed: restarting the access point (visitors reconnect in a few seconds)"
  systemctl restart parleybox.target
else
  systemctl restart parleybox.service
fi
# lets the Quarterdeck switch SSH on and off (only if SSH isn't enabled at boot)
systemctl restart parleybox-ssh.path

sleep 2
say "Status"
systemctl --no-pager --lines=0 status parleybox.service parleybox-hostapd.service parleybox-dnsmasq.service 2>/dev/null | grep -E 'service|Active' || true
echo
say "Done."
if [ $UPGRADE -eq 1 ]; then
  echo "  Upgraded:      $OLD_REV -> $(cat "$LIB/REVISION")"
fi
echo "  Shared files:  $SRV/share   (uploads in $SRV/share/uploads)"
echo "  Config:        $ETC/parleybox.conf   (set quarterdeck_password to enable the admin page)"
echo "  Logs:          journalctl -u parleybox -u parleybox-hostapd -u parleybox-dnsmasq -f"
if [ $NETWORK -eq 1 ] && [ $UPGRADE -eq 0 ]; then
  echo "  Wi-Fi:         '$SSID' (open)  ->  http://$HOSTNAME_PORTAL/  or  http://$IP/"
  echo
  echo "  NOTE: $IFACE is now dedicated to the access point. If this was your only"
  echo "  network link, use Ethernet (or a second adapter) to manage the box."
fi
