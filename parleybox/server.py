"""HTTP server: routing, captive-portal handling, file serving, uploads."""

import base64
import hmac
import json
import logging
import mimetypes
import os
import secrets
import shutil
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import pages
from .config import Config
from .multipart import MultipartError, MultipartReader, parse_boundary
from .store import BoardStore, ChatStore, VisitorStore
from .util import clip, sanitize_filename, unique_path

log = logging.getLogger("parleybox")

STATIC_DIR = Path(__file__).parent / "web"

# URLs that phones/laptops probe to decide whether they are online. Answering
# with a redirect makes the OS pop its "sign in to network" page -> our portal.
CAPTIVE_PATHS = {
    "/generate_204", "/gen_204",                    # Android / Chrome OS
    "/hotspot-detect.html", "/library/test/success.html",  # Apple
    "/ncsi.txt", "/connecttest.txt", "/redirect",   # Windows
    "/canonical.html", "/success.txt",              # Firefox
    "/check_network_status.txt", "/nm-check.txt",   # NetworkManager
    "/kindle-wifi/wifistub.html", "/mobile/status.php",
}

IMAGE_TYPES = {
    "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp",
}
BOARD_IMAGE_MAX = 8 * 1024 * 1024
FORM_FIELD_MAX = 64 * 1024


class App:
    """Shared state for all request handler threads."""

    def __init__(self, cfg: Config, dev: bool = False):
        self.cfg = cfg
        self.dev = dev
        cfg.ensure_dirs()
        self.board_images = cfg.data_path / "board_images"
        self.board_images.mkdir(parents=True, exist_ok=True)
        self.chat = ChatStore(cfg.data_path / "chat.jsonl", cfg.chat_history)
        self.board = BoardStore(cfg.data_path / "board.json", self.board_images,
                                cfg.board_max_threads, cfg.board_max_replies)
        self.visitors = VisitorStore(cfg.data_path / "visitors.json")
        self.allowed_hosts = {cfg.hostname.lower(), "localhost", *[h.lower() for h in cfg.extra_hosts]}
        self.portal_url = f"http://{cfg.hostname}/"
        self.started = time.time()
        self.secret = secrets.token_bytes(32)
        self.overrides_path = cfg.data_path / "quarterdeck.json"
        self._apply_overrides()

    # -- quarterdeck ------------------------------------------------------
    OVERRIDABLE = ("uploads_enabled", "chat_enabled", "board_enabled", "site_name", "motd")

    def _apply_overrides(self) -> None:
        """Runtime settings changed from the Quarterdeck persist in the data dir,
        because /etc is read-only for the service (ProtectSystem=strict)."""
        if not self.overrides_path.exists():
            return
        try:
            d = json.loads(self.overrides_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for k in self.OVERRIDABLE:
            if k in d:
                setattr(self.cfg, k, d[k])

    def save_overrides(self, values: dict) -> None:
        d = {k: values[k] for k in self.OVERRIDABLE if k in values}
        for k, v in d.items():
            setattr(self.cfg, k, v)
        tmp = self.overrides_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.overrides_path)

    def csrf_token(self) -> str:
        return hmac.new(self.secret, b"quarterdeck", "sha256").hexdigest()[:32]

    def check_password(self, authorization: str | None) -> bool:
        if not self.cfg.quarterdeck_password or not authorization:
            return False
        scheme, _, cred = authorization.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            user_pass = base64.b64decode(cred.strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        _, _, password = user_pass.partition(":")
        return hmac.compare_digest(password.encode(), self.cfg.quarterdeck_password.encode())

    def cargo_index(self, limit: int = 300) -> tuple:
        """Flat list of every file in the hold, newest first."""
        root = self.cfg.share_path
        items = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.startswith(".") or fn.endswith(".part"):
                    continue
                fp = Path(dirpath) / fn
                try:
                    st = fp.stat()
                except OSError:
                    continue
                items.append({"rel": str(fp.relative_to(root)), "size": st.st_size, "mtime": st.st_mtime})
        items.sort(key=lambda e: e["mtime"], reverse=True)
        return items[:limit], len(items)

    def info(self) -> dict:
        cargo, total = self.cargo_index(limit=10**9)
        usage = shutil.disk_usage(self.cfg.share_path)
        up = int(time.time() - self.started)
        d, h, m = up // 86400, (up % 86400) // 3600, (up % 3600) // 60
        threads, posts = self.board.count()
        return {
            "cargo_count": total, "cargo_bytes": sum(e["size"] for e in cargo),
            "disk": {"total": usage.total, "free": usage.free},
            "chat_count": self.chat.count(), "threads": threads, "posts": posts,
            "uptime": f"{d}d {h}h {m}m" if d else f"{h}h {m}m",
        }

    def host_ok(self, host: str) -> bool:
        if self.dev or not self.cfg.captive_portal:
            return True
        host = (host or "").split(":")[0].strip("[]").lower()
        if host in self.allowed_hosts:
            return True
        return host.replace(".", "").isdigit() or ":" in host  # raw IP literal


class Handler(BaseHTTPRequestHandler):
    server_version = "ParleyBox"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 120
    app: App  # set by make_server

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        log.info("%s %s", self.client_address[0], fmt % args)

    @property
    def cfg(self) -> Config:
        return self.app.cfg

    def _client_ip(self) -> str:
        return self.client_address[0]

    def send_html(self, html: str, code: int = 200, extra: dict | None = None):
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, obj, code: int = 200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_text(self, text: str, code: int = 200):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def redirect(self, url: str, code: int = 302):
        self.send_response(code)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def error(self, code: int, text: str):
        self.send_html(pages.error_page(self.cfg, code, text), code)

    def _stats(self) -> dict:
        return self.app.visitors.stats()

    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    # ------------------------------------------------------------ dispatch
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        host = self.headers.get("Host", "")
        if path in CAPTIVE_PATHS or not self.app.host_ok(host):
            self.redirect(self.app.portal_url)
            return
        self.app.visitors.hit(self._client_ip())
        q = self._query()
        try:
            if path == "/":
                self.send_html(pages.home(self.cfg, self._stats(), q.get("msg", "")))
            elif path.startswith("/static/"):
                self.serve_file(STATIC_DIR, path[len("/static/"):], cache=True)
            elif path == "/cargo":
                self.redirect("/cargo/", 301)
            elif path.startswith("/cargo/"):
                self.serve_share(path[len("/cargo/"):], q)
            elif path == "/files" or path.startswith("/files/"):
                self.redirect("/cargo/" + quote(path[len("/files/"):]), 301)
            elif path == "/quarterdeck":
                self.serve_quarterdeck(q)
            elif path == "/chat" and self.cfg.chat_enabled:
                self.send_html(pages.chat_page(self.cfg, self.app.chat.recent(200), self._stats()))
            elif path == "/api/chat" and self.cfg.chat_enabled:
                since = int(q.get("since", "0") or 0)
                wait = min(float(q.get("wait", "0") or 0), 30.0)
                msgs = self.app.chat.since(since, wait)
                self.send_json({"messages": msgs, "last_id": self.app.chat.last_id()})
            elif path == "/board" and self.cfg.board_enabled:
                self.send_html(pages.board_index(self.cfg, self.app.board.threads(), self._stats(),
                                                 q.get("msg", ""), q.get("err") == "1"))
            elif path.startswith("/board/") and self.cfg.board_enabled:
                self.serve_thread(path[len("/board/"):], q)
            elif path.startswith("/board-img/") and self.cfg.board_enabled:
                self.serve_file(self.app.board_images, path[len("/board-img/"):], sandbox=True)
            elif path == "/about":
                self.send_html(pages.about(self.cfg, self._stats()))
            elif path == "/api/status":
                self.send_json({"name": self.cfg.site_name, "hostname": self.cfg.hostname,
                                "uploads": self.cfg.uploads_enabled, "chat": self.cfg.chat_enabled,
                                "board": self.cfg.board_enabled, "time": time.time(), **self._stats()})
            elif path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.error(404, "Nothing at this address.")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        if not self.app.host_ok(self.headers.get("Host", "")):
            self.redirect(self.app.portal_url)
            return
        self.app.visitors.hit(self._client_ip())
        try:
            if path in ("/parley", "/upload"):
                self.handle_upload()
            elif path.startswith("/quarterdeck/"):
                self.handle_quarterdeck(path[len("/quarterdeck/"):])
            elif path == "/api/chat" and self.cfg.chat_enabled:
                self.handle_chat_post()
            elif path == "/board" and self.cfg.board_enabled:
                self.handle_board_post(None)
            elif path.startswith("/board/") and self.cfg.board_enabled:
                tid = path[len("/board/"):].strip("/")
                if not tid.isdigit():
                    self.error(404, "No such thread.")
                    return
                self.handle_board_post(int(tid))
            else:
                self.error(404, "Nothing at this address.")
        except MultipartError as e:
            self.close_connection = True
            self.error(400, f"Bad upload: {e}")
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------ files
    def _resolve(self, root: Path, rel: str) -> Path | None:
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." or "\x00" in p for p in parts):
            return None
        target = Path(os.path.realpath(root.joinpath(*parts))) if parts else Path(os.path.realpath(root))
        root_real = Path(os.path.realpath(root))
        if target != root_real and root_real not in target.parents:
            return None
        return target

    def serve_share(self, rel: str, q: dict):
        target = self._resolve(self.cfg.share_path, rel)
        if target is None or not target.exists():
            self.error(404, "No such file.")
            return
        if target.is_dir():
            if not rel.endswith("/") and rel:
                self.redirect("/cargo/" + quote(rel) + "/", 301)
                return
            entries = []
            try:
                for e in os.scandir(target):
                    if e.name.startswith(".") or e.name.endswith(".part"):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    entries.append({"name": e.name, "is_dir": e.is_dir(),
                                    "size": st.st_size, "mtime": st.st_mtime})
            except PermissionError:
                self.error(403, "Not allowed.")
                return
            entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            self.send_html(pages.cargo_listing(self.cfg, rel.strip("/"), entries, self._stats(),
                                              q.get("msg", ""), q.get("err") == "1"))
        else:
            self.send_file(target, sandbox=True)

    def serve_file(self, root: Path, rel: str, cache: bool = False, sandbox: bool = False):
        target = self._resolve(root, rel)
        if target is None or not target.is_file():
            self.error(404, "No such file.")
            return
        self.send_file(target, cache=cache, sandbox=sandbox)

    def send_file(self, path: Path, cache: bool = False, sandbox: bool = False):
        """Serve a file with single-range support so video seeking works."""
        try:
            st = path.stat()
            f = path.open("rb")
        except OSError:
            self.error(404, "No such file.")
            return
        with f:
            size = st.st_size
            ctype, _ = mimetypes.guess_type(str(path))
            ctype = ctype or "application/octet-stream"
            if ctype.startswith("text/"):
                ctype += "; charset=utf-8"
            start, end = 0, size - 1
            status = 200
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes=") and size > 0:
                spec = rng[6:].split(",")[0].strip()
                a, _, b = spec.partition("-")
                try:
                    if a == "":
                        n = int(b)
                        start = max(size - n, 0)
                    else:
                        start = int(a)
                        if b:
                            end = min(int(b), size - 1)
                    if start > end or start >= size:
                        raise ValueError
                    status = 206
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            length = end - start + 1 if size else 0
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", self.date_time_string(st.st_mtime))
            self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            if sandbox:
                # user-supplied content must never run scripts against our origin
                self.send_header("Content-Security-Policy", "sandbox")
                if ctype.startswith(("application/", "text/html")) and not ctype.startswith("application/pdf"):
                    self.send_header("Content-Disposition",
                                     f"attachment; filename*=UTF-8''{quote(path.name)}")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD" or length == 0:
                return
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ------------------------------------------------------------ uploads
    def _multipart(self, max_bytes: int) -> MultipartReader:
        ctype = self.headers.get("Content-Type", "")
        boundary = parse_boundary(ctype)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise MultipartError("Content-Length required")
        if length > max_bytes:
            raise MultipartError(f"upload too large (limit {max_bytes // (1024 * 1024)} MB)")
        return MultipartReader(self.rfile, boundary, length)

    def handle_upload(self):
        if not self.cfg.uploads_enabled:
            self.error(403, "No parley: the captain has closed the hold to new cargo.")
            return
        ajax = self.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            reader = self._multipart(self.cfg.max_upload_bytes + FORM_FIELD_MAX)
        except MultipartError as e:
            self.close_connection = True
            if ajax:
                self.send_text(str(e), 413)
            else:
                self.redirect(f"/cargo/?err=1&msg={quote(str(e))}", 303)
            return
        saved = []
        upload_dir = self.cfg.upload_path
        upload_dir.mkdir(parents=True, exist_ok=True)
        for part in reader:
            if not part.is_file or part.name != "file":
                part.drain()
                continue
            if not part.filename:
                part.drain()
                continue
            name = sanitize_filename(part.filename)
            tmp = upload_dir / f".{secrets.token_hex(6)}.part"
            written = 0
            try:
                with tmp.open("wb") as out:
                    for chunk in part.chunks():
                        written += len(chunk)
                        if written > self.cfg.max_upload_bytes:
                            raise MultipartError("file exceeds upload limit")
                        out.write(chunk)
                dest = unique_path(upload_dir, name)
                os.replace(tmp, dest)
                saved.append(dest.name)
                log.info("upload %s (%d bytes) from %s", dest.name, written, self._client_ip())
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
        if not saved:
            raise MultipartError("no file received")
        if ajax:
            self.send_json({"saved": saved})
        else:
            self.redirect(f"/cargo/uploads/?msg={quote('Cargo stowed: ' + ', '.join(saved))}", 303)

    # ------------------------------------------------------------ chat
    def _read_form(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > FORM_FIELD_MAX:
            raise MultipartError("form too large")
        body = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}

    def handle_chat_post(self):
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/"):
            form = {}
            for part in self._multipart(FORM_FIELD_MAX * 4):
                form[part.name] = part.text(FORM_FIELD_MAX)
        else:
            form = self._read_form()
        name = clip(form.get("name", ""), 32)
        text = clip(form.get("text", ""), 500)
        if not text:
            self.send_json({"error": "empty message"}, 400)
            return
        try:
            m = self.app.chat.post(name, text, self._client_ip())
        except ValueError as e:
            self.send_json({"error": str(e)}, 429)
            return
        if self.headers.get("Accept", "").startswith("text/html"):
            self.redirect("/chat", 303)
        else:
            self.send_json({"ok": True, "message": m})

    # ------------------------------------------------------------ board
    def serve_thread(self, rel: str, q: dict):
        tid = rel.strip("/")
        t = self.app.board.get(int(tid)) if tid.isdigit() else None
        if t is None:
            self.error(404, "No such thread.")
            return
        self.send_html(pages.board_thread(self.cfg, t, self._stats(), q.get("msg", ""), q.get("err") == "1"))

    def handle_board_post(self, thread_id: int | None):
        back = "/board" if thread_id is None else f"/board/{thread_id}"
        ctype = self.headers.get("Content-Type", "")
        fields: dict = {}
        image = ""
        try:
            if ctype.startswith("multipart/"):
                reader = self._multipart(BOARD_IMAGE_MAX + 4 * FORM_FIELD_MAX)
                for part in reader:
                    if part.is_file:
                        if part.name == "image" and self.cfg.board_images and part.filename:
                            image = self._save_board_image(part)
                        else:
                            part.drain()
                    else:
                        fields[part.name] = part.text(FORM_FIELD_MAX)
            else:
                fields = self._read_form()
        except MultipartError as e:
            self.close_connection = True
            self.redirect(f"{back}?err=1&msg={quote(str(e))}", 303)
            return
        name = clip(fields.get("name", ""), 32)
        text = clip(fields.get("text", ""), 4000)
        if not text:
            self.redirect(f"{back}?err=1&msg={quote('Write something first.')}", 303)
            return
        if thread_id is None:
            subject = clip(fields.get("subject", ""), 100) or text[:60]
            t = self.app.board.create(subject, name, text, image)
            self.redirect(f"/board/{t['id']}", 303)
        else:
            p = self.app.board.reply(thread_id, name, text, image)
            if p is None:
                self.error(404, "No such thread.")
                return
            self.redirect(f"/board/{thread_id}#p{p['id']}", 303)

    def _save_board_image(self, part) -> str:
        ext = IMAGE_TYPES.get(part.content_type.split(";")[0].strip().lower())
        if not ext:
            part.drain()
            raise MultipartError("only jpeg/png/gif/webp images are accepted")
        fname = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}.{ext}"
        tmp = self.app.board_images / (fname + ".part")
        written = 0
        with tmp.open("wb") as out:
            for chunk in part.chunks():
                written += len(chunk)
                if written > BOARD_IMAGE_MAX:
                    out.close()
                    tmp.unlink(missing_ok=True)
                    raise MultipartError("image too large (max 8 MB)")
                out.write(chunk)
        if written == 0:
            tmp.unlink(missing_ok=True)
            return ""
        os.replace(tmp, self.app.board_images / fname)
        return fname


    # ------------------------------------------------------------ quarterdeck
    def _require_captain(self) -> bool:
        if not self.cfg.quarterdeck_password:
            self.error(404, "Nothing at this address.")
            return False
        if self.app.check_password(self.headers.get("Authorization")):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Quarterdeck", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        body = b"Captain's password required.\n"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return False

    def serve_quarterdeck(self, q: dict):
        if not self._require_captain():
            return
        cargo, total = self.app.cargo_index()
        info = self.app.info()
        info["cargo_truncated"] = total > len(cargo)
        self.send_html(pages.quarterdeck(
            self.cfg, info, cargo, self.app.chat.recent(100), self.app.board.threads(),
            self.app.csrf_token(), self._stats(), q.get("msg", ""), q.get("err") == "1",
        ), extra={"Cache-Control": "no-store, private"})

    def handle_quarterdeck(self, action: str):
        if not self._require_captain():
            return
        form = self._read_form()
        if not hmac.compare_digest(form.get("token", ""), self.app.csrf_token()):
            self.redirect(f"/quarterdeck?err=1&msg={quote('Stale form token; try again.')}", 303)
            return
        msg, err = "Done.", False
        if action == "cargo/delete":
            rel = form.get("path", "")
            target = self._resolve(self.cfg.share_path, rel)
            if target is None or not target.is_file():
                msg, err = "No such cargo.", True
            else:
                target.unlink()
                log.info("quarterdeck: %s thrown overboard by %s", rel, self._client_ip())
                msg = f"{rel} thrown overboard."
        elif action == "chat/delete":
            ok = form.get("id", "").isdigit() and self.app.chat.delete(int(form["id"]))
            msg, err = ("Message deleted.", False) if ok else ("No such message.", True)
        elif action == "chat/clear":
            self.app.chat.clear()
            msg = "Chat log wiped."
        elif action == "board/delete":
            t, p = form.get("thread", ""), form.get("post", "")
            ok = t.isdigit() and p.isdigit() and self.app.board.delete_post(int(t), int(p))
            msg, err = ("Post deleted.", False) if ok else ("No such post.", True)
        elif action == "settings":
            values = {
                "uploads_enabled": form.get("uploads_enabled") == "1",
                "chat_enabled": form.get("chat_enabled") == "1",
                "board_enabled": form.get("board_enabled") == "1",
                "site_name": clip(form.get("site_name", ""), 40) or self.cfg.site_name,
                "motd": form.get("motd", "").replace("\r", "").strip()[:2000] or self.cfg.motd,
            }
            self.app.save_overrides(values)
            msg = "Ship's articles updated."
        else:
            self.error(404, "Nothing at this address.")
            return
        self.redirect(f"/quarterdeck?{'err=1&' if err else ''}msg={quote(msg)}", 303)


def make_server(cfg: Config, dev: bool = False) -> ThreadingHTTPServer:
    app = App(cfg, dev=dev)
    handler = type("BoundHandler", (Handler,), {"app": app})
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    server = ThreadingHTTPServer((cfg.listen, cfg.port), handler)
    server.app = app  # type: ignore[attr-defined]
    return server


def serve_forever(cfg: Config, dev: bool = False) -> None:
    server = make_server(cfg, dev)
    log.info("ParleyBox %s listening on http://%s:%d/ (share=%s data=%s)",
             "dev" if dev else "", cfg.listen, cfg.port, cfg.share_path, cfg.data_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.visitors.close()  # type: ignore[attr-defined]
        server.server_close()
