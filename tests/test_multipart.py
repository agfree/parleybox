import io
import unittest

from piratebox.multipart import MultipartError, MultipartReader, parse_boundary


def build(parts, boundary=b"BOUND"):
    out = b"preamble junk\r\n"
    for headers, body in parts:
        out += b"--" + boundary + b"\r\n"
        for k, v in headers:
            out += k + b": " + v + b"\r\n"
        out += b"\r\n" + body + b"\r\n"
    out += b"--" + boundary + b"--\r\nepilogue"
    return out


class MultipartTests(unittest.TestCase):
    def test_parse_boundary(self):
        self.assertEqual(parse_boundary('multipart/form-data; boundary="abc"'), b"abc")
        self.assertEqual(parse_boundary("multipart/form-data; boundary=----x"), b"----x")
        with self.assertRaises(MultipartError):
            parse_boundary("application/x-www-form-urlencoded")
        with self.assertRaises(MultipartError):
            parse_boundary("multipart/form-data")

    def test_fields_and_files(self):
        body = build([
            ([(b"Content-Disposition", b'form-data; name="name"')], b"alex"),
            ([(b"Content-Disposition", b'form-data; name="file"; filename="a b.txt"'),
              (b"Content-Type", b"text/plain")], b"hello\r\nworld"),
        ])
        r = MultipartReader(io.BytesIO(body), b"BOUND", len(body), chunk_size=7)
        parts = list(r)
        self.assertEqual(len(parts), 2)
        # first part must be drained automatically when we skipped it via iteration
        self.assertEqual(parts[0].name, "name")
        self.assertFalse(parts[0].is_file)
        self.assertEqual(parts[1].name, "file")
        self.assertEqual(parts[1].filename, "a b.txt")
        self.assertEqual(parts[1].content_type, "text/plain")

    def test_streaming_large_body_across_chunks(self):
        payload = bytes(range(256)) * 4000  # ~1 MB, contains partial-marker lookalikes
        payload += b"\r\n--BOUN"  # nearly a delimiter, must be preserved
        body = build([([(b"Content-Disposition", b'form-data; name="file"; filename="x"')], payload)])
        for chunk in (1, 5, 64, 1000, 65536):
            r = MultipartReader(io.BytesIO(body), b"BOUND", len(body), chunk_size=chunk)
            p = r.next_part()
            got = b"".join(p.chunks())
            self.assertEqual(got, payload, f"chunk_size={chunk}")
            self.assertIsNone(r.next_part())

    def test_read_text_and_limit(self):
        body = build([([(b"Content-Disposition", b'form-data; name="t"')], "héllo".encode())])
        r = MultipartReader(io.BytesIO(body), b"BOUND", len(body))
        self.assertEqual(r.next_part().text(), "héllo")
        r = MultipartReader(io.BytesIO(body), b"BOUND", len(body))
        with self.assertRaises(MultipartError):
            r.next_part().read(limit=2)

    def test_truncated_body(self):
        body = build([([(b"Content-Disposition", b'form-data; name="f"; filename="x"')], b"data")])
        body = body[:-15]
        r = MultipartReader(io.BytesIO(body), b"BOUND", len(body))
        p = r.next_part()
        with self.assertRaises(MultipartError):
            b"".join(p.chunks())

    def test_content_length_respected(self):
        body = build([([(b"Content-Disposition", b'form-data; name="f"')], b"abc")])
        stream = io.BytesIO(body + b"EXTRA BYTES NOT PART OF BODY")
        r = MultipartReader(stream, b"BOUND", len(body))
        self.assertEqual(r.next_part().read(), b"abc")
        self.assertIsNone(r.next_part())
        self.assertEqual(stream.read(), b"EXTRA BYTES NOT PART OF BODY")

    def test_empty_body(self):
        body = b"--BOUND--\r\n"
        r = MultipartReader(io.BytesIO(body), b"BOUND", len(body))
        self.assertIsNone(r.next_part())


if __name__ == "__main__":
    unittest.main()
