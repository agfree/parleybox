"""Small helpers shared across the package."""

import html
import re
import time
import unicodedata
from pathlib import Path

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WS = re.compile(r"\s+")


def sanitize_filename(name: str, fallback: str = "upload") -> str:
    """Reduce an untrusted filename to a safe basename."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    name = _CONTROL.sub("", name)
    name = _WS.sub(" ", name).strip()
    name = name.lstrip(".")
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 16:
            name = stem[: 200 - len(ext) - 1] + "." + ext
        else:
            name = name[:200]
    return name or fallback


def unique_path(directory: Path, name: str) -> Path:
    """Return a path in ``directory`` for ``name`` that does not exist yet."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    n = 1
    while True:
        candidate = directory / (f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}")
        if not candidate.exists():
            return candidate
        n += 1


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def esc(s) -> str:
    return html.escape(str(s), quote=True)


_URL = re.compile(r"(https?://[^\s<>\"']+)")


def render_text(text: str) -> str:
    """Escape user text for HTML, keep newlines, mark quotes and links."""
    out = []
    for line in text.split("\n"):
        e = esc(line)
        e = _URL.sub(r'<a href="\1" rel="noreferrer">\1</a>', e)
        if line.startswith(">"):
            e = f'<span class="quote">{e}</span>'
        out.append(e)
    return "<br>".join(out)


def clip(s: str, n: int) -> str:
    s = _CONTROL.sub("", s.replace("\r", "")) if "\n" not in s else s.replace("\r", "")
    s = s.strip()
    return s[:n]
