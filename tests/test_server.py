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
        self.assertIn(b"Welcome to ParleyBox", data)
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
        r, data = self.req("GET", "/files/")
        self.assertEqual(r.status, 200)
        self.assertIn(b'href="sub/"', data)
        r, data = self.req("GET", "/files/sub/doc.txt")
        self.assertEqual(r.status, 200)
        self.assertEqual(data, b"0123456789")
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
        r, data = self.req("GET", "/files/sub/doc.txt", headers={"Range": "bytes=2-4"})
        self.assertEqual(r.status, 206)
        self.assertEqual(data, b"234")
        self.assertEqual(r.getheader("Content-Range"), "bytes 2-4/10")
        r, data = self.req("GET", "/files/sub/doc.txt", headers={"Range": "bytes=-3"})
        self.assertEqual(data, b"789")
        r, _ = self.req("GET", "/files/sub/doc.txt", headers={"Range": "bytes=50-60"})
        self.assertEqual(r.status, 416)
        r, _ = self.req("HEAD", "/files/sub/doc.txt")
        self.assertEqual(r.getheader("Content-Length"), "10")
        r, _ = self.req("GET", "/files/sub")
        self.assertEqual(r.status, 301)

    def test_user_html_is_sandboxed(self):
        r, _ = self.req("GET", "/files/page.html")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Security-Policy"), "sandbox")
        self.assertIn("attachment", r.getheader("Content-Disposition"))

    def test_traversal_blocked(self):
        for p in ("/files/../../etc/passwd", "/files/..%2F..%2Fetc/passwd", "/static/../server.py",
                  "/board-img/../board.json"):
            r, _ = self.req("GET", p)
            self.assertEqual(r.status, 404, p)

    def test_upload_and_limits(self):
        body, ctype = multipart({"junk": "x"}, {"file": ("../../hack.bin", b"\x00\x01\x02" * 100, "application/octet-stream")})
        r, data = self.req("POST", "/upload", body, {"Content-Type": ctype, "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status, 200, data)
        self.assertEqual(json.loads(data)["saved"], ["hack.bin"])
        self.assertEqual((self.cfg.upload_path / "hack.bin").read_bytes(), b"\x00\x01\x02" * 100)
        # same name again -> deduplicated
        r, data = self.req("POST", "/upload", body, {"Content-Type": ctype, "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(json.loads(data)["saved"], ["hack-1.bin"])
        # plain-form upload redirects
        r, _ = self.req("POST", "/upload", body, {"Content-Type": ctype})
        self.assertEqual(r.status, 303)
        self.assertTrue(r.getheader("Location").startswith("/files/uploads/"))
        # oversize rejected by Content-Length before reading anything
        r, data = self.req("POST", "/upload", b"", {"Content-Type": ctype, "Content-Length": str(50 * 1024 * 1024),
                                                    "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status, 413)
        # oversize inside a lying Content-Length is still caught by the byte counter
        big = b"A" * (self.cfg.max_upload_bytes + 10)
        body, ctype = multipart({}, {"file": ("big.bin", big, "application/octet-stream")})
        r, data = self.req("POST", "/upload", body, {"Content-Type": ctype, "Content-Length": str(len(body) // 2 + 100),
                                                     "X-Requested-With": "XMLHttpRequest"})
        self.assertIn(r.status, (400, 413))
        self.assertFalse((self.cfg.upload_path / "big.bin").exists())
        self.assertEqual([p for p in self.cfg.upload_path.iterdir() if p.suffix == ".part"], [])
        # no file part
        body, ctype = multipart({"file": "not a file"}, {})
        r, _ = self.req("POST", "/upload", body, {"Content-Type": ctype})
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

    def test_status(self):
        r, data = self.req("GET", "/api/status")
        d = json.loads(data)
        self.assertEqual(d["hostname"], "parleybox.test")
        self.assertGreaterEqual(d["total"], 1)


if __name__ == "__main__":
    unittest.main()
