"""Configuration loading for PirateBox (INI file with sane defaults)."""

import configparser
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_MOTD = (
    "Welcome aboard. This box is not connected to the internet.\n"
    "Everything here is shared anonymously by the people around you.\n"
    "Take what you need, leave something behind."
)


@dataclass
class Config:
    site_name: str = "PirateBox"
    hostname: str = "piratebox.lan"
    listen: str = "0.0.0.0"
    port: int = 80
    share_dir: str = "/srv/piratebox/share"
    upload_dir: str = ""  # defaults to <share_dir>/uploads
    data_dir: str = "/srv/piratebox/data"
    uploads_enabled: bool = True
    max_upload_mb: int = 2048
    chat_enabled: bool = True
    chat_history: int = 500
    board_enabled: bool = True
    board_images: bool = True
    board_max_threads: int = 200
    board_max_replies: int = 500
    motd: str = DEFAULT_MOTD
    captive_portal: bool = True
    extra_hosts: list = field(default_factory=list)

    @property
    def share_path(self) -> Path:
        return Path(self.share_dir).resolve()

    @property
    def upload_path(self) -> Path:
        if self.upload_dir:
            return Path(self.upload_dir).resolve()
        return self.share_path / "uploads"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb) * 1024 * 1024

    def ensure_dirs(self) -> None:
        for p in (self.share_path, self.upload_path, self.data_path):
            p.mkdir(parents=True, exist_ok=True)


def load(path: str | None = None, **overrides) -> Config:
    """Load a Config from an INI file. Missing file -> defaults."""
    cfg = Config()
    if path and os.path.exists(path):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding="utf-8")
        if parser.has_section("piratebox"):
            sec = parser["piratebox"]
            for f in fields(Config):
                if f.name not in sec:
                    continue
                raw = sec.get(f.name)
                if f.type is bool or isinstance(getattr(cfg, f.name), bool):
                    setattr(cfg, f.name, sec.getboolean(f.name))
                elif isinstance(getattr(cfg, f.name), int):
                    setattr(cfg, f.name, sec.getint(f.name))
                elif isinstance(getattr(cfg, f.name), list):
                    setattr(cfg, f.name, [x.strip() for x in raw.split(",") if x.strip()])
                else:
                    setattr(cfg, f.name, raw.replace("\\n", "\n"))
    for k, v in overrides.items():
        if v is not None:
            setattr(cfg, k, v)
    return cfg
