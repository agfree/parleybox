import tempfile
import threading
import time
import unittest
from pathlib import Path

from parleybox.store import BoardStore, ChatStore, VisitorStore


class ChatTests(unittest.TestCase):
    def test_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "chat.jsonl"
            c = ChatStore(p, history=3)
            for i in range(5):
                c.post("n", f"m{i}", ip=f"10.0.0.{i}")
            self.assertEqual([m["text"] for m in c.recent()], ["m2", "m3", "m4"])
            c2 = ChatStore(p, history=3)
            self.assertEqual(c2.last_id(), 5)
            self.assertEqual([m["text"] for m in c2.recent()], ["m2", "m3", "m4"])
            self.assertEqual(c2.post("", "x")["id"], 6)

    def test_rate_limit(self):
        with tempfile.TemporaryDirectory() as d:
            c = ChatStore(Path(d) / "c.jsonl")
            c.post("", "a", ip="1.1.1.1")
            with self.assertRaises(ValueError):
                c.post("", "b", ip="1.1.1.1")
            c.post("", "b", ip="2.2.2.2")  # different client is fine

    def test_long_poll_wakes(self):
        with tempfile.TemporaryDirectory() as d:
            c = ChatStore(Path(d) / "c.jsonl")
            self.assertEqual(c.since(0, wait=0.05), [])
            t = threading.Timer(0.1, lambda: c.post("", "late"))
            t.start()
            start = time.monotonic()
            got = c.since(0, wait=3)
            self.assertLess(time.monotonic() - start, 2)
            self.assertEqual(got[0]["text"], "late")


class BoardTests(unittest.TestCase):
    def test_threads_replies_prune(self):
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "img"
            img.mkdir()
            b = BoardStore(Path(d) / "board.json", img, max_threads=2)
            (img / "old.png").write_bytes(b"x")
            t1 = b.create("first", "", "hello", image="old.png")
            t2 = b.create("second", "me", "world")
            self.assertEqual([t["id"] for t in b.threads()], [t2["id"], t1["id"]])
            b.reply(t1["id"], "", "bump")
            self.assertEqual(b.threads()[0]["id"], t1["id"])
            b.create("third", "", "prunes t2")
            ids = {t["id"] for t in b.threads()}
            self.assertNotIn(t2["id"], ids)
            self.assertTrue((img / "old.png").exists())  # t1 survived
            b.create("fourth", "", "prunes t1")
            self.assertFalse((img / "old.png").exists())
            self.assertIsNone(b.reply(999, "", "nope"))
            b2 = BoardStore(Path(d) / "board.json", img)
            self.assertEqual(len(b2.threads()), 2)


class VisitorTests(unittest.TestCase):
    def test_counts(self):
        with tempfile.TemporaryDirectory() as d:
            v = VisitorStore(Path(d) / "v.json")
            v.hit("a"); v.hit("a"); v.hit("b")
            self.assertEqual(v.stats(), {"total": 2, "online": 2})
            v.close()
            v2 = VisitorStore(Path(d) / "v.json")
            self.assertEqual(v2.stats()["total"], 2)


if __name__ == "__main__":
    unittest.main()
