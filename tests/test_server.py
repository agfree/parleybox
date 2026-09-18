"""End-to-end tests against a real server on a loopback port."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from parleybox.config import Config
from parleybox.server import make_server


def multipart(fields, files, boundary="TESTBOUNDARY"):
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n".encode() + v.encode() + b"\r\n"
    for k, (fname, data, ctype) in files.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = Config(listen="127.0.0.1", port=0, share_dir=str(root / "share"),
                         data_dir=str(root / "data"), max_upload_mb=1, hostname="parleybox.test")
        cls.server = make_server(cls.cfg, dev=False)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        (root / "share" / "sub").mkdir(parents=True)
        (root / "share" / "sub" / "doc.txt").write_text("0123456789")
        (root / "share" / "page.html").write_text("<script>alert(1)</script>")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, host="parleybox.test"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_home_and_about(self):
        r, data = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"Welcome aboard ParleyBox", data)
        r, _ = self.req("GET", "/about")
        self.assertEqual(r.status, 200)

    def test_captive_probe_redirects(self):
        for p in ("/generate_204", "/hotspot-detect.html", "/ncsi.txt", "/canonical.html"):
            r, _ = self.req("GET", p, host="connectivitycheck.gstatic.com")
            self.assertEqual(r.status, 302, p)
            self.assertEqual(r.getheader("Location"), "http://parleybox.test/")

    def test_foreign_host_redirects_but_ip_allowed(self):
        r, _ = self.req("GET", "/", host="www.example.com")
        self.assertEqual(r.status, 302)
        r, _ = self.req("GET", "/", host="192.168.77.1")
        self.assertEqual(r.status, 200)
        r, _ = self.req("GET", "/", host="192.168.77.1:80")
        self.assertEqual(r.status, 200)

    def test_listing_and_download(self):
        r, data = self.req("GET", "/cargo/")
        self.assertEqual(r.status, 200)
        self.assertIn(b'href="sub/"', data)
        r, data = self.req("GET", "/cargo/sub/doc.txt")
        self.assertEqual(r.status, 200)
        self.assertEqual(data, b"0123456789")
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
        r, data = self.req("GET", "/cargo/sub/doc.txt", headers={"Range": "bytes=2-4"})
        self.assertEqual(r.status, 206)
        self.assertEqual(data, b"234")
        self.assertEqual(r.getheader("Content-Range"), "bytes 2-4/10")
        r, data = self.req("GET", "/cargo/sub/doc.txt", headers={"Range": "bytes=-3"})
        self.assertEqual(data, b"789")
        r, _ = self.req("GET", "/cargo/sub/doc.txt", headers={"Range": "bytes=50-60"})
        self.assertEqual(r.status, 416)
        r, _ = self.req("HEAD", "/cargo/sub/doc.txt")
        self.assertEqual(r.getheader("Content-Length"), "10")
        r, _ = self.req("GET", "/cargo/sub")
        self.assertEqual(r.status, 301)

    def test_user_html_is_sandboxed(self):
        r, _ = self.req("GET", "/cargo/page.html")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Security-Policy"), "sandbox")
        self.assertIn("attachment", r.getheader("Content-Disposition"))

    def test_traversal_blocked(self):
        for p in ("/cargo/../../etc/passwd", "/cargo/..%2F..%2Fetc/passwd", "/static/../server.py",
                  "/board-img/../board.json"):
            r, _ = self.req("GET", p)
            self.assertEqual(r.status, 404, p)

    def test_upload_and_limits(self):
        body, ctype = multipart({"junk": "x"}, {"file": ("../../hack.bin", b"\x00\x01\x02" * 100, "application/octet-stream")})
        r, data = self.req("POST", "/parley", body, {"Content-Type": ctype, "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status, 200, data)
        self.assertEqual(json.loads(data)["saved"], ["hack.bin"])
        self.assertEqual((self.cfg.upload_path / "hack.bin").read_bytes(), b"\x00\x01\x02" * 100)
        # same name again -> deduplicated
        r, data = self.req("POST", "/parley", body, {"Content-Type": ctype, "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(json.loads(data)["saved"], ["hack-1.bin"])
        # plain-form upload redirects
        r, _ = self.req("POST", "/parley", body, {"Content-Type": ctype})
        self.assertEqual(r.status, 303)
        self.assertTrue(r.getheader("Location").startswith("/cargo/uploads/"))
        # oversize rejected by Content-Length before reading anything
        r, data = self.req("POST", "/parley", b"", {"Content-Type": ctype, "Content-Length": str(50 * 1024 * 1024),
                                                    "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status, 413)
        # oversize inside a lying Content-Length is still caught by the byte counter
        big = b"A" * (self.cfg.max_upload_bytes + 10)
        body, ctype = multipart({}, {"file": ("big.bin", big, "application/octet-stream")})
        r, data = self.req("POST", "/parley", body, {"Content-Type": ctype, "Content-Length": str(len(body) // 2 + 100),
                                                     "X-Requested-With": "XMLHttpRequest"})
        self.assertIn(r.status, (400, 413))
        self.assertFalse((self.cfg.upload_path / "big.bin").exists())
        self.assertEqual([p for p in self.cfg.upload_path.iterdir() if p.suffix == ".part"], [])
        # no file part
        body, ctype = multipart({"file": "not a file"}, {})
        r, _ = self.req("POST", "/parley", body, {"Content-Type": ctype})
        self.assertEqual(r.status, 400)

    def test_chat(self):
        r, data = self.req("POST", "/api/chat", b"name=a%3Cb&text=hi+%3Cthere%3E",
                           {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 200)
        m = json.loads(data)["message"]
        r, data = self.req("GET", f"/api/chat?since={m['id'] - 1}")
        self.assertEqual(json.loads(data)["messages"][-1]["text"], "hi <there>")
        r, data = self.req("GET", "/chat")
        self.assertIn(b"hi &lt;there&gt;", data)
        self.assertNotIn(b"hi <there>", data)
        r, _ = self.req("POST", "/api/chat", b"text=", {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 400)

    def test_board(self):
        body, ctype = multipart({"subject": "<s>", "name": "", "text": "first <post>"}, {})
        r, _ = self.req("POST", "/board", body, {"Content-Type": ctype})
        self.assertEqual(r.status, 303)
        loc = r.getheader("Location")
        r, data = self.req("GET", loc)
        self.assertIn(b"&lt;s&gt;", data)
        self.assertIn(b"first &lt;post&gt;", data)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        body, ctype = multipart({"text": "with pic"}, {"image": ("p.png", png, "image/png")})
        r, _ = self.req("POST", loc, body, {"Content-Type": ctype})
        self.assertEqual(r.status, 303)
        r, data = self.req("GET", loc)
        self.assertIn(b"/board-img/", data)
        img = data.split(b'/board-img/')[1].split(b'"')[0].decode()
        r, data = self.req("GET", "/board-img/" + img)
        self.assertEqual(data, png)
        self.assertEqual(r.getheader("Content-Security-Policy"), "sandbox")
        body, ctype = multipart({"text": "bad"}, {"image": ("x.exe", b"MZ", "application/octet-stream")})
        r, _ = self.req("POST", loc, body, {"Content-Type": ctype})
        self.assertIn("err=1", r.getheader("Location"))
        r, _ = self.req("GET", "/board/999")
        self.assertEqual(r.status, 404)

    def test_signin_window_note(self):
        webview = ("Mozilla/5.0 (Linux; Android 14; Pixel 7 Build/UQ1A.240205.004; wv) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Version/4.0 Chrome/122.0.6261.119 Mobile Safari/537.36")
        chrome = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36")
        for path, url in (("/cargo/", b"http://parleybox.test/cargo/"), ("/board", b"http://parleybox.test/board")):
            _, data = self.req("GET", path, headers={"User-Agent": webview})
            self.assertIn(b"signin-note", data, path)
            self.assertIn(url, data, path)
            _, data = self.req("GET", path, headers={"User-Agent": chrome})
            self.assertNotIn(b"signin-note", data, path)

    def test_legacy_files_path_redirects(self):
        r, _ = self.req("GET", "/files/sub/doc.txt")
        self.assertEqual(r.status, 301)
        self.assertEqual(r.getheader("Location"), "/cargo/sub/doc.txt")

    def test_quarterdeck_hidden_when_disabled(self):
        r, data = self.req("GET", "/quarterdeck")
        self.assertEqual(r.status, 404)
        r, data = self.req("GET", "/")
        self.assertNotIn(b"Quarterdeck", data)

    def test_status(self):
        r, data = self.req("GET", "/api/status")
        d = json.loads(data)
        self.assertEqual(d["hostname"], "parleybox.test")
        self.assertGreaterEqual(d["total"], 1)


if __name__ == "__main__":
    unittest.main()


class QuarterdeckTests(unittest.TestCase):
    """Separate server with the captain's password set."""

    @classmethod
    def setUpClass(cls):
        import base64
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.cfg = Config(listen="127.0.0.1", port=0, share_dir=str(root / "share"),
                         data_dir=str(root / "data"), hostname="parleybox.test",
                         quarterdeck_password="yo-ho-ho")
        cls.server = make_server(cls.cfg, dev=False)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.auth = {"Authorization": "Basic " + base64.b64encode(b"captain:yo-ho-ho").decode()}
        cls.badauth = {"Authorization": "Basic " + base64.b64encode(b"captain:nope").decode()}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "parleybox.test"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def token(self):
        r, data = self.req("GET", "/quarterdeck", headers=self.auth)
        self.assertEqual(r.status, 200)
        return data.split(b'name="token" value="')[1].split(b'"')[0].decode(), data

    def post(self, action, fields, headers=None):
        from urllib.parse import urlencode
        h = {"Content-Type": "application/x-www-form-urlencoded"}
        h.update(headers if headers is not None else self.auth)
        return self.req("POST", "/quarterdeck/" + action, urlencode(fields).encode(), h)

    def test_auth_required(self):
        r, _ = self.req("GET", "/quarterdeck")
        self.assertEqual(r.status, 401)
        self.assertIn("Quarterdeck", r.getheader("WWW-Authenticate"))
        r, _ = self.req("GET", "/quarterdeck", headers=self.badauth)
        self.assertEqual(r.status, 401)
        r, data = self.req("GET", "/", headers=None)
        self.assertIn(b"Quarterdeck", data)  # link shown when enabled
        r, _ = self.post("chat/clear", {"token": "x"}, headers=self.badauth)
        self.assertEqual(r.status, 401)

    def test_csrf_token_required(self):
        r, _ = self.post("chat/clear", {"token": "wrong"})
        self.assertEqual(r.status, 303)
        self.assertIn("err=1", r.getheader("Location"))

    def test_cargo_overboard(self):
        f = self.cfg.share_path / "uploads" / "loot.bin"
        f.write_bytes(b"gold")
        tok, page = self.token()
        self.assertIn(b"uploads/loot.bin", page)
        r, _ = self.post("cargo/delete", {"token": tok, "path": "uploads/loot.bin"})
        self.assertEqual(r.status, 303)
        self.assertFalse(f.exists())
        # traversal and missing files are refused
        r, _ = self.post("cargo/delete", {"token": tok, "path": "../../etc/passwd"})
        self.assertIn("err=1", r.getheader("Location"))

    def test_chat_moderation(self):
        app = self.server.app
        a = app.chat.post("", "keep")
        b = app.chat.post("", "delete me", ip="9.9.9.9")
        tok, _ = self.token()
        self.post("chat/delete", {"token": tok, "id": str(b["id"])})
        self.assertEqual([m["text"] for m in app.chat.recent()], ["keep"])
        self.post("chat/clear", {"token": tok})
        self.assertEqual(app.chat.recent(), [])

    def test_board_moderation(self):
        app = self.server.app
        t = app.board.create("s", "", "op")
        p = app.board.reply(t["id"], "", "reply")
        tok, _ = self.token()
        self.post("board/delete", {"token": tok, "thread": str(t["id"]), "post": str(p["id"])})
        self.assertEqual(len(app.board.get(t["id"])["posts"]), 1)
        self.post("board/delete", {"token": tok, "thread": str(t["id"]), "post": str(t["id"])})
        self.assertIsNone(app.board.get(t["id"]))

    def test_settings_override_and_persist(self):
        tok, _ = self.token()
        r, _ = self.post("settings", {"token": tok, "chat_enabled": "1", "board_enabled": "1",
                                      "site_name": "Black Pearl", "motd": "Arr"})
        self.assertEqual(r.status, 303)
        self.assertFalse(self.cfg.uploads_enabled)
        self.assertEqual(self.cfg.site_name, "Black Pearl")
        r, _ = self.req("POST", "/parley", b"", {"Content-Type": "multipart/form-data; boundary=x", "Content-Length": "0"})
        self.assertEqual(r.status, 403)
        r, data = self.req("GET", "/")
        self.assertIn(b"Welcome aboard Black Pearl", data)
        self.assertIn(b"Arr", data)
        # a fresh App reading the same data dir picks the overrides up
        from parleybox.server import App
        fresh = App(Config(share_dir=self.cfg.share_dir, data_dir=self.cfg.data_dir, quarterdeck_password="x"))
        self.assertEqual(fresh.cfg.site_name, "Black Pearl")
        self.assertFalse(fresh.cfg.uploads_enabled)
        self.post("settings", {"token": tok, "uploads_enabled": "1", "chat_enabled": "1", "board_enabled": "1"})
        self.assertTrue(self.cfg.uploads_enabled)
