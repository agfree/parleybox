"""Persistence for chat, forum board and visitor counter.

Everything is plain JSON / JSON-lines on disk so the data survives a reboot
and can be inspected or backed up with ordinary tools.
"""

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class ChatStore:
    """Append-only shoutbox with long-poll support."""

    def __init__(self, path: Path, history: int = 500):
        self.path = path
        self.history = history
        self._msgs: deque = deque(maxlen=history)
        self._cond = threading.Condition()
        self._next_id = 1
        self._last_post: dict = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._msgs.append(m)
                self._next_id = max(self._next_id, int(m.get("id", 0)) + 1)

    def post(self, name: str, text: str, ip: str = "") -> dict:
        with self._cond:
            now = time.time()
            if ip and now - self._last_post.get(ip, 0) < 0.5:
                raise ValueError("slow down")
            self._last_post[ip] = now
            m = {"id": self._next_id, "ts": now, "name": name, "text": text}
            self._next_id += 1
            self._msgs.append(m)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
            self._cond.notify_all()
            return m

    def since(self, last_id: int, wait: float = 0.0) -> list:
        deadline = time.monotonic() + wait
        with self._cond:
            while True:
                new = [m for m in self._msgs if m["id"] > last_id]
                if new or wait <= 0:
                    return new
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)

    def recent(self, n: int = 100) -> list:
        with self._cond:
            return list(self._msgs)[-n:]

    def last_id(self) -> int:
        with self._cond:
            return self._msgs[-1]["id"] if self._msgs else 0


class BoardStore:
    """Tiny anonymous forum / imageboard: threads with replies."""

    def __init__(self, path: Path, image_dir: Path, max_threads: int = 200,
                 max_replies: int = 500):
        self.path = path
        self.image_dir = image_dir
        self.max_threads = max_threads
        self.max_replies = max_replies
        self._lock = threading.Lock()
        self._data = {"next_id": 1, "threads": []}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        _atomic_write(self.path, json.dumps(self._data, ensure_ascii=False, indent=1))

    def _new_id(self) -> int:
        i = self._data["next_id"]
        self._data["next_id"] = i + 1
        return i

    def threads(self) -> list:
        with self._lock:
            return sorted(self._data["threads"], key=lambda t: t["bumped"], reverse=True)

    def get(self, thread_id: int) -> Optional[dict]:
        with self._lock:
            for t in self._data["threads"]:
                if t["id"] == thread_id:
                    return t
        return None

    def create(self, subject: str, name: str, text: str, image: str = "") -> dict:
        with self._lock:
            now = time.time()
            tid = self._new_id()
            t = {
                "id": tid, "subject": subject, "bumped": now,
                "posts": [{"id": tid, "name": name, "text": text, "ts": now, "image": image}],
            }
            self._data["threads"].append(t)
            self._prune()
            self._save()
            return t

    def reply(self, thread_id: int, name: str, text: str, image: str = "") -> Optional[dict]:
        with self._lock:
            for t in self._data["threads"]:
                if t["id"] == thread_id:
                    now = time.time()
                    p = {"id": self._new_id(), "name": name, "text": text, "ts": now, "image": image}
                    t["posts"].append(p)
                    if len(t["posts"]) <= self.max_replies:
                        t["bumped"] = now
                    self._save()
                    return p
        return None

    def _prune(self) -> None:
        if len(self._data["threads"]) <= self.max_threads:
            return
        self._data["threads"].sort(key=lambda t: t["bumped"])
        dead = self._data["threads"][: len(self._data["threads"]) - self.max_threads]
        self._data["threads"] = self._data["threads"][len(dead):]
        for t in dead:
            for p in t["posts"]:
                if p.get("image"):
                    try:
                        (self.image_dir / p["image"]).unlink()
                    except OSError:
                        pass


class VisitorStore:
    """Counts distinct client addresses; 'online' = seen in the last 5 min."""

    ONLINE_WINDOW = 300

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._seen: dict = {}
        self._total = 0
        self._dirty = False
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                self._total = int(d.get("total", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                pass

    def hit(self, ip: str) -> None:
        with self._lock:
            now = time.time()
            if ip not in self._seen:
                self._total += 1
                self._dirty = True
            self._seen[ip] = now
            if self._dirty and (self._total % 5 == 0 or self._total < 10):
                self._flush()

    def _flush(self) -> None:
        try:
            _atomic_write(self.path, json.dumps({"total": self._total}))
            self._dirty = False
        except OSError:
            pass

    def stats(self) -> dict:
        with self._lock:
            now = time.time()
            online = sum(1 for ts in self._seen.values() if now - ts < self.ONLINE_WINDOW)
            return {"total": self._total, "online": online}

    def close(self) -> None:
        with self._lock:
            if self._dirty:
                self._flush()
