"""A small streaming multipart/form-data parser (RFC 7578).

Python's ``cgi`` module is deprecated and buffers uploads in memory; this
reader streams part bodies in chunks so multi-gigabyte uploads work on a
Raspberry Pi with a few hundred MB of RAM.
"""

from email.message import Message
from typing import BinaryIO, Iterator, Optional


class MultipartError(ValueError):
    pass


def parse_boundary(content_type: str) -> bytes:
    msg = Message()
    msg["content-type"] = content_type
    if msg.get_content_type() != "multipart/form-data":
        raise MultipartError("expected multipart/form-data")
    boundary = msg.get_param("boundary")
    if not boundary:
        raise MultipartError("missing multipart boundary")
    return boundary.encode("latin-1")


class Part:
    """One part of a multipart body. Consume ``chunks()`` before calling
    ``MultipartReader.next_part()`` again (the reader does this for you if
    you don't)."""

    def __init__(self, reader: "MultipartReader", headers: dict):
        self._reader = reader
        self.headers = headers
        self._consumed = False
        msg = Message()
        for k, v in headers.items():
            msg[k] = v
        self.name = msg.get_param("name", header="content-disposition")
        self.filename = msg.get_param("filename", header="content-disposition")
        self.content_type = headers.get("content-type", "text/plain")

    @property
    def is_file(self) -> bool:
        return self.filename is not None

    def chunks(self) -> Iterator[bytes]:
        if self._consumed:
            return
        self._consumed = True
        yield from self._reader._iter_body()

    def read(self, limit: int = 1 << 20) -> bytes:
        buf = bytearray()
        for chunk in self.chunks():
            buf += chunk
            if len(buf) > limit:
                raise MultipartError("form field too large")
        return bytes(buf)

    def text(self, limit: int = 1 << 20) -> str:
        return self.read(limit).decode("utf-8", "replace")

    def drain(self) -> None:
        for _ in self.chunks():
            pass


class MultipartReader:
    def __init__(self, stream: BinaryIO, boundary: bytes, length: int,
                 chunk_size: int = 64 * 1024):
        self._stream = stream
        self._delim = b"--" + boundary
        self._remaining = length
        self._chunk = chunk_size
        self._buf = b""
        self._eof = False
        self._started = False
        self._finished = False
        self._current: Optional[Part] = None

    # -- low level -------------------------------------------------------
    def _fill(self) -> None:
        if self._eof:
            return
        want = min(self._chunk, self._remaining)
        data = self._stream.read(want) if want > 0 else b""
        if not data:
            self._eof = True
            return
        self._remaining -= len(data)
        self._buf += data

    def _read_line(self) -> bytes:
        while True:
            idx = self._buf.find(b"\r\n")
            if idx >= 0:
                line = self._buf[:idx]
                self._buf = self._buf[idx + 2:]
                return line
            if self._eof:
                raise MultipartError("unexpected end of multipart body")
            if len(self._buf) > 64 * 1024:
                raise MultipartError("multipart header line too long")
            self._fill()

    def _iter_body(self) -> Iterator[bytes]:
        marker = b"\r\n" + self._delim
        keep = len(marker) - 1
        while True:
            idx = self._buf.find(marker)
            if idx >= 0:
                if idx:
                    yield self._buf[:idx]
                self._buf = self._buf[idx + 2:]  # leave buf at the delimiter line
                return
            if self._eof:
                raise MultipartError("unterminated multipart part")
            if len(self._buf) > keep:
                yield self._buf[:-keep]
                self._buf = self._buf[-keep:]
            self._fill()

    # -- public ----------------------------------------------------------
    def next_part(self) -> Optional[Part]:
        if self._finished:
            return None
        if self._current is not None:
            self._current.drain()
            self._current = None
        if not self._started:
            self._started = True
            # skip preamble
            while True:
                line = self._read_line()
                if line == self._delim:
                    break
                if line == self._delim + b"--":
                    self._finished = True
                    return None
        else:
            line = self._read_line()
            if line == self._delim + b"--":
                self._finished = True
                return None
            if line != self._delim:
                raise MultipartError("malformed multipart delimiter")
        headers: dict = {}
        while True:
            line = self._read_line()
            if not line:
                break
            if b":" not in line:
                raise MultipartError("malformed part header")
            k, v = line.split(b":", 1)
            headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
        self._current = Part(self, headers)
        return self._current

    def __iter__(self) -> Iterator[Part]:
        while True:
            part = self.next_part()
            if part is None:
                return
            yield part
