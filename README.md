# PirateBox

A from-scratch recreation of [PirateBox](https://en.wikipedia.org/wiki/PirateBox):
a small computer that broadcasts an **open Wi-Fi network with no internet**, where
anyone in range can anonymously share files, chat, and leave messages on a board.

The original project (David Darts, 2011; maintained by Matthias Strubel until 2019)
was shut down as routers locked their firmware and browsers forced HTTPS.
This is a clean reimplementation of the same idea for hardware you can still buy:
a Raspberry Pi (or any Debian box) with a Wi-Fi adapter that supports AP mode.

```
                       _.--.
                   _.-'_:-'||          Connect to "PirateBox - Share Freely"
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
- **File sharing**: browse and download the shared folder, with HTTP range support so videos seek.
- **Uploads**: streamed straight to disk with a progress bar, size limits, filename sanitizing,
  and no memory blow-up on a 512 MB Pi.
- **Chat / shoutbox**: anonymous, long-polling, survives reboots.
- **Message board**: anonymous threads and replies with optional image attachments.
- **Visitor counter**: how many people are aboard now and how many have ever visited.
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
git clone <this repo> && cd pirate_bbs
sudo ./install.sh --ssid "PirateBox - Share Freely" --country US
```

Options: `--iface wlan0` `--ip 192.168.77.1` `--channel 6` `--country US` `--no-network`.

The installer:

1. installs `hostapd`, `dnsmasq`, `rfkill`, `iw`;
2. copies the server to `/usr/local/lib/piratebox` and config to `/etc/piratebox/`;
3. tells NetworkManager (or dhcpcd) to leave the Wi-Fi interface alone;
4. installs and starts four systemd units grouped under `piratebox.target`:
   `piratebox-net` (static IP), `piratebox-hostapd`, `piratebox-dnsmasq`, `piratebox` (web).

**The Wi-Fi interface becomes the access point**, so manage the Pi over Ethernet or a second adapter.
Use `--no-network` to install only the web server (for example to serve it on your LAN).

Shared files live in `/srv/piratebox/share`; uploads go to `/srv/piratebox/share/uploads`.
Drop files there with `scp` or a USB drive. Chat and board state live in `/srv/piratebox/data`.

```sh
journalctl -u piratebox -u piratebox-hostapd -u piratebox-dnsmasq -f   # logs
sudo systemctl restart piratebox.target                                 # restart everything
sudo ./uninstall.sh [--purge]                                           # remove
```

## Configuration

Edit `/etc/piratebox/piratebox.conf` (see `etc/piratebox.conf` for every key) and
`sudo systemctl restart piratebox`. You can rename the box, change the message of the day,
disable uploads, chat or the board, cap upload size, and set the portal hostname.
Wi-Fi settings live in `/etc/piratebox/hostapd.conf` and `dnsmasq.conf`.

## Run it anywhere (dev mode)

```sh
python3 -m piratebox --dev            # http://localhost:8080/, ./share and ./data
python3 -m unittest discover -s tests # 27 tests, no extra packages
```

Dev mode skips the captive-portal host check so `localhost` works.

## How the captive portal works

There is no internet, so we cannot break HTTPS and do not try. Instead:

- dnsmasq resolves **every** hostname to the box, so `http://anything/` lands on the portal.
- Operating systems probe known URLs (`/generate_204`, `/hotspot-detect.html`, `/ncsi.txt`, ...)
  to detect connectivity. The server answers those with a redirect to `http://piratebox.lan/`,
  which makes the OS show its "sign in to network" page with the portal inside it.
- DHCP option 114 advertises the portal URL for clients that support RFC 8910.
- Requests with an unknown `Host` header are redirected too, so old bookmarks and
  browser home pages end up at the portal instead of a timeout.

Typing an `https://` address will still fail with a certificate error; that is the trade-off
every offline portal makes. The About page tells visitors to use `http://`.

## Layout

```
piratebox/        Python package (server, multipart parser, stores, pages, web assets)
etc/              config and systemd unit templates
bin/piratebox-net interface bring-up script
install.sh        Debian/Raspberry Pi OS installer
uninstall.sh
tests/            unittest suite
```

## License

GPL-3.0-or-later, the same license as the original PirateBox scripts.
