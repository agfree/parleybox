import tempfile
import threading
import time
import unittest
from pathlib import Path

from parleybox.store import BoardStore, ChatStore, StatsStore, VisitorStore


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
            v2.hit("a")
            v2.reset()
            self.assertEqual(v2.stats(), {"total": 0, "online": 0})
            self.assertEqual(VisitorStore(Path(d) / "v.json").stats()["total"], 0)
            v2.hit("a")
            self.assertEqual(v2.stats(), {"total": 1, "online": 1})


class StatsTests(unittest.TestCase):
    def test_counts_persist_and_reset(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "stats.json"
            s = StatsStore(path)
            s.visit("a", 1); s.visit("a", 1); s.visit("b", 3); s.visit("c", 2)
            s.download("movie.mp4", 1000, True)
            s.download("movie.mp4", 500, False)  # seeking: bytes, not a new download
            s.download("doc.txt", 10, True)
            s.download("doc.txt", 10, True)
            s.add(uploads=1, up_bytes=42)
            s.add(chat=1); s.add(posts=2)
            r = s.report()
            t = r["totals"]
            self.assertEqual((t["visitors"], t["peak"], t["downloads"], t["down_bytes"]), (3, 3, 3, 1520))
            self.assertEqual((t["uploads"], t["up_bytes"], t["chat"], t["posts"]), (1, 42, 1, 2))
            self.assertEqual(r["top"], [("doc.txt", 2), ("movie.mp4", 1)])
            self.assertEqual(len(r["days"]), 1)
            s.forget("doc.txt")
            self.assertEqual(s.report()["top"], [("movie.mp4", 1)])
            s.close()
            s2 = StatsStore(path)
            self.assertEqual(s2.report()["totals"]["downloads"], 3)
            self.assertEqual(s2.report()["peak"]["n"], 3)
            s2.reset()
            self.assertEqual(StatsStore(path).report()["totals"]["downloads"], 0)

    def test_old_days_and_files_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            s = StatsStore(Path(d) / "stats.json")
            s.KEEP_FILES = 3
            for i in range(5):
                s.download(f"f{i}", 1, True)
            s.download("f4", 1, True)
            self.assertEqual(len(s._data["files"]), 3)
            self.assertIn("f4", s._data["files"])
            s._data["days"] = {f"2020-01-{i:02d}": {} for i in range(1, 32)}
            s.KEEP_DAYS = 5
            s._today = ""
            s.add(chat=1)
            self.assertEqual(len(s._data["days"]), 5)

    def test_corrupt_file_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "stats.json"
            path.write_text("[1, 2]")
            self.assertEqual(StatsStore(path).report()["totals"]["downloads"], 0)


if __name__ == "__main__":
    unittest.main()
