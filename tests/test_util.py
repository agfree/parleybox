import tempfile
import unittest
from pathlib import Path

from parleybox.util import clip, human_size, render_text, sanitize_filename, unique_path


class UtilTests(unittest.TestCase):
    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("C:\\Users\\x\\report.pdf"), "report.pdf")
        self.assertEqual(sanitize_filename(".hidden"), "hidden")
        self.assertEqual(sanitize_filename("..."), "upload")
        self.assertEqual(sanitize_filename(""), "upload")
        self.assertEqual(sanitize_filename("a\x00b\nc.txt"), "abc.txt")
        self.assertEqual(sanitize_filename("  spaced   out .txt "), "spaced out .txt")
        long = "x" * 300 + ".tar.gz"
        s = sanitize_filename(long)
        self.assertLessEqual(len(s), 200)
        self.assertTrue(s.endswith(".gz"))

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(unique_path(d, "a.txt"), d / "a.txt")
            (d / "a.txt").touch()
            self.assertEqual(unique_path(d, "a.txt"), d / "a-1.txt")
            (d / "a-1.txt").touch()
            self.assertEqual(unique_path(d, "a.txt"), d / "a-2.txt")
            (d / "noext").touch()
            self.assertEqual(unique_path(d, "noext"), d / "noext-1")

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1023), "1023 B")
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertEqual(human_size(3 * 1024 ** 3), "3.0 GB")

    def test_render_text(self):
        out = render_text("<b>hi</b>\n>quote\nsee http://parleybox.lan/x?a=1&b=2")
        self.assertIn("&lt;b&gt;hi&lt;/b&gt;", out)
        self.assertIn('<span class="quote">&gt;quote</span>', out)
        self.assertIn('<a href="http://parleybox.lan/x?a=1&amp;b=2"', out)
        self.assertNotIn("<b>", out)

    def test_clip(self):
        self.assertEqual(clip("  hi\r\n", 10), "hi")
        self.assertEqual(clip("x" * 20, 5), "xxxxx")
        self.assertEqual(clip("a\x00b", 10), "ab")


if __name__ == "__main__":
    unittest.main()
