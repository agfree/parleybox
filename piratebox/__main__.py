"""Command line entry point: ``python3 -m piratebox``."""

import argparse
import logging
import sys

from . import __version__, config
from .server import serve_forever


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="piratebox", description="PirateBox offline sharing server")
    ap.add_argument("-c", "--config", default="/etc/piratebox/piratebox.conf", help="INI config file")
    ap.add_argument("--dev", action="store_true",
                    help="developer mode: ./share and ./data, port 8080, no captive-portal host checks")
    ap.add_argument("--listen", help="bind address")
    ap.add_argument("--port", type=int, help="bind port")
    ap.add_argument("--share", dest="share_dir", help="shared files directory")
    ap.add_argument("--data", dest="data_dir", help="state directory (chat, board, counters)")
    ap.add_argument("--hostname", help="portal hostname (default piratebox.lan)")
    ap.add_argument("--version", action="version", version=f"piratebox {__version__}")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)
    overrides = {
        "listen": args.listen, "port": args.port, "share_dir": args.share_dir,
        "data_dir": args.data_dir, "hostname": args.hostname,
    }
    if args.dev:
        overrides.setdefault("port", None)
        overrides["port"] = overrides["port"] or 8080
        overrides["share_dir"] = overrides["share_dir"] or "./share"
        overrides["data_dir"] = overrides["data_dir"] or "./data"
        cfg = config.load(args.config if args.config != "/etc/piratebox/piratebox.conf" else None, **overrides)
    else:
        cfg = config.load(args.config, **overrides)
    serve_forever(cfg, dev=args.dev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
