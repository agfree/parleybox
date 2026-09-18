# ParleyBox

A from-scratch recreation of [PirateBox](https://en.wikipedia.org/wiki/PirateBox):
a small computer that broadcasts an **open Wi-Fi network with no internet**, where
anyone in range can anonymously share files, chat, and leave messages on a board.

The vocabulary is nautical: visitors come **aboard**, files are **cargo**, sharing one
is a **parley**, and the captain runs the ship from the **Quarterdeck**.

The original project (David Darts, 2011; maintained by Matthias Strubel until 2019)
was shut down as routers locked their firmware and browsers forced HTTPS.
This is a clean reimplementation of the same idea for hardware you can still buy:
a Raspberry Pi (or any Debian box) with a Wi-Fi adapter that supports AP mode.

```
                       _.--.
                   _.-'_:-'||          Connect to "ParleyBox - Share Freely"
               _.-'_.-::::'||          Your phone says "sign in to network"
          _.-:'_.-::::::'  ||          ...and you're aboard.
        .'`-.-:::::::'     ||
       /.'`;|:::::::'      ||_
      ||   ||::::::'     _.;._'-._
      ||   ||:::::'  _.-!oo @.!-._'-.
      \'.  ||:::::.-!()oo @!()@.-'_.|
       '.'-;|:.-'.&$@.& ()$%-'o.'\U||
```

## Features

- **Open access point** with DHCP and a DNS server that answers every name with the box.
- **Captive portal** using the connectivity probes of Android, iOS/macOS, Windows,
  Firefox and NetworkManager, plus the RFC 8910 DHCP option, so phones pop the portal automatically.
- **Cargo hold** (`/cargo/`): browse and download the shared folder, with HTTP range support so videos seek.
- **Parley** (uploads): streamed straight to disk with a progress bar, size limits, filename sanitizing,
  and no memory blow-up on a 512 MB Pi.
- **Chat / shoutbox**: anonymous, long-polling, survives reboots.
- **Message board**: anonymous threads and replies with optional image attachments.
- **Aboard counter**: how many people are aboard now and how many have ever come aboard.
- **Quarterdeck** (`/quarterdeck`): the captain's page. Throw cargo overboard, delete chat
  messages or board posts, switch uploads/chat/board on and off, rename the ship and edit the
  message of the day, all from a phone. Off and hidden until `quarterdeck_password` is set.
- **Zero dependencies**: Python 3.11+ standard library only. No pip, no database, no CDN,
  no JavaScript frameworks. Everything is served from the box.
- Hardened a little: user content is served with `Content-Security-Policy: sandbox`,
  path traversal is blocked, the web server runs unprivileged under systemd.

## Hardware

Tested target: Raspberry Pi (Zero 2 W, 3, 4, 5) running Raspberry Pi OS / Debian 12+.
Any Linux box whose Wi-Fi driver supports AP mode works (`iw list | grep -A8 "Supported interface modes"`
should list `AP`). Add a USB SSD or big SD card for the shared folder.

## Install on a Pi

```sh
git clone https://github.com/agfree/parleybox && cd parleybox
sudo ./install.sh --ssid "ParleyBox - Share Freely" --country US
```

Options: `--iface wlan0` `--ip 192.168.77.1` `--channel 6` `--country US` `--no-network`.

The installer:

1. installs `hostapd`, `dnsmasq`, `rfkill`, `iw`;
2. copies the server to `/usr/local/lib/parleybox` and config to `/etc/parleybox/`;
3. tells NetworkManager (or dhcpcd) to leave the Wi-Fi interface alone;
4. installs and starts four systemd units grouped under `parleybox.target`:
   `parleybox-net` (static IP), `parleybox-hostapd`, `parleybox-dnsmasq`, `parleybox` (web).

**The Wi-Fi interface becomes the access point**, so manage the Pi over Ethernet or a second adapter.
Use `--no-network` to install only the web server (for example to serve it on your LAN).

Shared files live in `/srv/parleybox/share`; uploads go to `/srv/parleybox/share/uploads`.
Drop files there with `scp` or a USB drive. Chat and board state live in `/srv/parleybox/data`.

```sh
journalctl -u parleybox -u parleybox-hostapd -u parleybox-dnsmasq -f   # logs
sudo systemctl restart parleybox.target                                 # restart everything
sudo ./uninstall.sh [--purge]                                           # remove
```

## Upgrading

The box doesn't need internet. Pull the new version on a laptop, join the laptop to the
ParleyBox Wi-Fi, and copy it over SSH (the box is `192.168.77.1` unless you chose another `--ip`;
SSH must be enabled on the box, for example with Raspberry Pi Imager's "Enable SSH" option):

```sh
git pull                                                   # on the laptop, while it has internet
rsync -a --delete ./ you@192.168.77.1:parleybox/           # then, on the ParleyBox Wi-Fi
ssh -t you@192.168.77.1 sudo ./parleybox/install.sh --upgrade
```

If the box has its own way online (Ethernet, a USB Ethernet adapter on a Pi Zero, or a
second Wi-Fi adapter), `git pull` and `sudo ./install.sh --upgrade` on the box works too.

`--upgrade` replaces the server code and systemd units and keeps everything else: the
Wi-Fi, DHCP and interface settings in `/etc/parleybox/`, `parleybox.conf`, the shared files,
chat, board and Quarterdeck settings. Only the web server restarts, so nobody is dropped
from the Wi-Fi. The access point restarts only if a new version changes its systemd units.
Wi-Fi settings are left exactly as they are, so changes to the config templates in a new
version are not applied; run the installer without `--upgrade` (and your options) to re-render them.

## Configuration

Edit `/etc/parleybox/parleybox.conf` (see `etc/parleybox.conf` for every key) and
`sudo systemctl restart parleybox`. You can rename the box, change the message of the day,
disable uploads, chat or the board, cap upload size, set the portal hostname, and set the
Quarterdeck password. Wi-Fi settings live in `/etc/parleybox/hostapd.conf` and `dnsmasq.conf`.

Settings changed on the Quarterdeck are saved to `/srv/parleybox/data/quarterdeck.json`
and override the config file. Delete that file to go back to the config file's values.

### About the Quarterdeck password

The box speaks plain `http` over an open Wi-Fi network, so the password travels in the clear
and anyone sniffing the air could grab it. Treat it as a latch, not a lock: use a throwaway
password, and keep anything you truly care about off the box.

## Run it anywhere (dev mode)

```sh
python3 -m parleybox --dev            # http://localhost:8080/, ./share and ./data
python3 -m unittest discover -s tests # no extra packages needed
```

Dev mode skips the captive-portal host check so `localhost` works.

## How the captive portal works

There is no internet, so we cannot break HTTPS and do not try. Instead:

- dnsmasq resolves **every** hostname to the box, so `http://anything/` lands on the portal.
- Operating systems probe known URLs (`/generate_204`, `/hotspot-detect.html`, `/ncsi.txt`, ...)
  to detect connectivity. The server answers those with a redirect to `http://parleybox.lan/`,
  which makes the OS show its "sign in to network" page with the portal inside it.
- DHCP option 114 advertises the portal URL for clients that support RFC 8910.
- Requests with an unknown `Host` header are redirected too, so old bookmarks and
  browser home pages end up at the portal instead of a timeout.

Typing an `https://` address will still fail with a certificate error; that is the trade-off
every offline portal makes. The About page tells visitors to use `http://`.

## Layout

```
parleybox/        Python package (server, multipart parser, stores, pages, web assets)
etc/              config and systemd unit templates
bin/parleybox-net interface bring-up script
install.sh        Debian/Raspberry Pi OS installer
uninstall.sh
tests/            unittest suite
```

## License

GPL-3.0-or-later, the same license as the original PirateBox scripts.
