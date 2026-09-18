"""HTML rendering. Plain string building, no external template engine,
no external assets: everything must work with zero internet access.

Vocabulary: visitors come *aboard*, files are *cargo*, sharing one is a
*parley*, and the captain runs things from the *Quarterdeck*."""

from urllib.parse import quote

from . import __version__
from .util import esc, fmt_time, human_size, render_text

FLAG = r"""
                       _.--.
                   _.-'_:-'||
               _.-'_.-::::'||
          _.-:'_.-::::::'  ||
        .'`-.-:::::::'     ||
       /.'`;|:::::::'      ||_
      ||   ||::::::'     _.;._'-._
      ||   ||:::::'  _.-!oo @.!-._'-.
      \'.  ||:::::.-!()oo @!()@.-'_.|
       '.'-;|:.-'.&$@.& ()$%-'o.'\U||
         `>'-.!@%()@'@_%-'_.-o _.|'||
          ||-._'-.@.-'_.-' _.-o  |'||
          ||=[ '-._.-\U/.-'    o |'||
          || '-.]=|| |'|      o  |'||
          ||      || |'|        _| ';
          ||      || |'|    _.-'_.-'
          |'-._   || |'|_.-'_.-'
           '-._'-.|| |' `_.-'
               '-.||_/.-'
"""

NAV = [
    ("/", "Deck"),
    ("/cargo/", "Cargo"),
    ("/chat", "Chat"),
    ("/board", "Board"),
    ("/about", "About"),
    ("/quarterdeck", "Quarterdeck"),
]


def layout(cfg, title: str, body: str, active: str = "", stats: dict | None = None,
           scripts: tuple = ()) -> str:
    hidden = set()
    if not cfg.chat_enabled:
        hidden.add("/chat")
    if not cfg.board_enabled:
        hidden.add("/board")
    if not cfg.quarterdeck_password:
        hidden.add("/quarterdeck")
    nav = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{label}</a>'
        for href, label in NAV if href not in hidden
    )
    st = ""
    if stats:
        st = f'<span class="stats">{stats["online"]} aboard now &middot; {stats["total"]} have come aboard</span>'
    js = "".join(f'<script src="/static/{s}" defer></script>' for s in scripts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} &middot; {esc(cfg.site_name)}</title>
<link rel="stylesheet" href="/static/style.css">
<link rel="icon" href="data:,">
{js}
</head>
<body>
<header>
  <a class="brand" href="/">&#9760; {esc(cfg.site_name)}</a>
  <nav>{nav}</nav>
  {st}
</header>
<main>
{body}
</main>
<footer>{esc(cfg.site_name)} &middot; offline &middot; anonymous &middot; no log of who you are &middot; parleybox v{__version__}</footer>
</body>
</html>
"""


def _flash(msg: str, error: bool = False) -> str:
    if not msg:
        return ""
    return f'<div class="notice{" error" if error else ""}">{esc(msg)}</div>'


def home(cfg, stats: dict, msg: str = "") -> str:
    cards = [
        ("/cargo/", "Cargo hold", "Browse what others have brought aboard."),
    ]
    if cfg.uploads_enabled:
        cards.append(("/cargo/#parley", "Parley", f"Share cargo of your own. Up to {cfg.max_upload_mb} MB a go."))
    if cfg.chat_enabled:
        cards.append(("/chat", "Chat", "Talk with whoever is aboard right now."))
    if cfg.board_enabled:
        cards.append(("/board", "Message board", "Leave a note that outlives your visit."))
    cards.append(("/about", "About", "What this vessel is and how it works."))
    grid = "".join(
        f'<a class="card" href="{href}"><b>{esc(t)}</b><span>{esc(d)}</span></a>' for href, t, d in cards
    )
    body = f"""
{_flash(msg)}
<pre class="flag">{esc(FLAG)}</pre>
<h1>Welcome aboard {esc(cfg.site_name)}</h1>
<pre class="motd">{esc(cfg.motd)}</pre>
<div class="grid">{grid}</div>
"""
    return layout(cfg, "Deck", body, "/", stats)


def _signin_note(cfg, path: str) -> str:
    """Android's "Sign in to network" window ignores file inputs, so point
    people at their real browser instead."""
    return (
        '<div class="notice signin-note">Nothing happens when you tap the file button? You are in the '
        "Wi-Fi sign-in window, which can't open the file picker. Close it (stay on the Wi-Fi), open "
        f'your browser and go to <b class="url">{esc(f"http://{cfg.hostname}{path}")}</b>. '
        "If it won't load, turn off mobile data.</div>"
    )


def parley_form(cfg, msg: str = "", error: bool = False, signin: bool = False) -> str:
    if not cfg.uploads_enabled:
        return _flash(msg, error)
    return f"""
<div class="panel" id="parley">
<h2>Parley</h2>
<p class="dim small">Bring cargo aboard for everyone within range.</p>
{_flash(msg, error)}
{_signin_note(cfg, "/cargo/") if signin else ""}
<form id="upload-form" method="post" action="/parley" enctype="multipart/form-data">
<label>Cargo (up to {cfg.max_upload_mb} MB per parley)</label>
<input type="file" name="file" multiple required>
<button type="submit">Parley</button>
<progress id="upload-progress" value="0" max="100" style="display:none"></progress>
<div id="upload-status" class="dim small"></div>
</form>
<p class="dim small">Stowed in <a href="/cargo/uploads/">/cargo/uploads/</a>. Anything you bring aboard is visible to everyone on this network.</p>
</div>
"""


def cargo_listing(cfg, rel: str, entries: list, stats: dict, msg: str = "", error: bool = False,
                  signin: bool = False) -> str:
    parts = [p for p in rel.split("/") if p]
    crumbs = ['<a href="/cargo/">cargo</a>']
    acc = ""
    for p in parts:
        acc += "/" + quote(p)
        crumbs.append(f'<a href="/cargo{acc}/">{esc(p)}</a>')
    rows = []
    if parts:
        rows.append('<tr><td><a href="../">../</a></td><td class="size"></td><td class="time"></td></tr>')
    for e in entries:
        href = quote(e["name"]) + ("/" if e["is_dir"] else "")
        label = esc(e["name"]) + ("/" if e["is_dir"] else "")
        size = "" if e["is_dir"] else human_size(e["size"])
        rows.append(
            f'<tr><td><a href="{href}">{label}</a></td>'
            f'<td class="size">{size}</td><td class="time">{fmt_time(e["mtime"])}</td></tr>'
        )
    if not entries:
        rows.append('<tr><td colspan="3" class="dim">The hold is empty. Nothing stowed here yet.</td></tr>')
    body = f"""
<h1>Cargo hold</h1>
<div class="crumbs">{" / ".join(crumbs)}</div>
<table class="files">
<tr><th>Name</th><th>Size</th><th class="time">Stowed</th></tr>
{"".join(rows)}
</table>
{parley_form(cfg, msg, error, signin)}
"""
    return layout(cfg, "Cargo", body, "/cargo/", stats, scripts=("upload.js",))


def chat_page(cfg, messages: list, stats: dict) -> str:
    lines = []
    for m in messages:
        n = m.get("name") or "anon"
        cls = "n" if m.get("name") else "n anon"
        lines.append(
            f'<p class="msg"><span class="t">{fmt_time(m["ts"])[-5:]}</span>'
            f'<span class="{cls}">{esc(n)}</span><span class="b">{esc(m["text"])}</span></p>'
        )
    last = messages[-1]["id"] if messages else 0
    body = f"""
<h1>Chat</h1>
<div id="chat-log" data-last-id="{last}">{"".join(lines)}</div>
<form id="chat-form" method="post" action="/api/chat">
  <div class="name"><label>Name (optional)</label><input type="text" id="chat-name" name="name" maxlength="32" autocomplete="off"></div>
  <div class="text"><label>Message</label><input type="text" id="chat-text" name="text" maxlength="500" autocomplete="off" autofocus></div>
  <button type="submit">Send</button>
</form>
<p class="dim small">Only whoever is aboard can read this. The box keeps the last {cfg.chat_history} messages. Nobody knows who you are.</p>
"""
    return layout(cfg, "Chat", body, "/chat", stats, scripts=("chat.js",))


def _post_html(p: dict) -> str:
    img = ""
    if p.get("image"):
        src = "/board-img/" + quote(p["image"])
        img = f'<a href="{src}"><img src="{src}" alt="attachment" loading="lazy"></a>'
    n = p.get("name") or "Anonymous"
    return (
        f'<div class="post" id="p{p["id"]}"><div class="meta"><span class="n">{esc(n)}</span> '
        f'&middot; {fmt_time(p["ts"])} &middot; <a href="#p{p["id"]}">No.{p["id"]}</a></div>'
        f'{img}<div class="body">{render_text(p["text"])}</div></div>'
    )


def _post_form(cfg, action: str, thread: bool, signin: bool = False) -> str:
    subj = '<label>Subject</label><input type="text" name="subject" maxlength="100" required>' if thread else ""
    img = ""
    if cfg.board_images:
        img = '<label>Image (optional, max 8 MB)</label><input type="file" name="image" accept="image/*">'
        if signin:
            img = _signin_note(cfg, action) + img
    return f"""
<div class="panel">
<h2>{"Start a thread" if thread else "Reply"}</h2>
<form method="post" action="{action}" enctype="multipart/form-data">
{subj}
<label>Name (optional)</label><input type="text" name="name" maxlength="32">
<label>Text</label><textarea name="text" maxlength="4000" required></textarea>
{img}
<button type="submit">{"Post thread" if thread else "Post reply"}</button>
</form>
</div>
"""


def board_index(cfg, threads: list, stats: dict, msg: str = "", error: bool = False,
                signin: bool = False) -> str:
    items = []
    for t in threads:
        op = t["posts"][0]
        replies = len(t["posts"]) - 1
        preview = esc(op["text"][:200]) + ("..." if len(op["text"]) > 200 else "")
        items.append(
            f'<div class="thread"><a class="subject" href="/board/{t["id"]}">{esc(t["subject"])}</a>'
            f'<div class="meta dim small">{esc(op.get("name") or "Anonymous")} &middot; {fmt_time(op["ts"])} '
            f'&middot; {replies} {"reply" if replies == 1 else "replies"}'
            f'{" &middot; [img]" if op.get("image") else ""}</div>'
            f'<div class="small">{preview}</div></div>'
        )
    if not items:
        items.append('<p class="dim">No threads yet. Start one.</p>')
    body = f"""
<h1>Message board</h1>
{_flash(msg, error)}
{"".join(items)}
{_post_form(cfg, "/board", thread=True, signin=signin)}
"""
    return layout(cfg, "Board", body, "/board", stats)


def board_thread(cfg, t: dict, stats: dict, msg: str = "", error: bool = False,
                 signin: bool = False) -> str:
    posts = "".join(_post_html(p) for p in t["posts"])
    body = f"""
<div class="crumbs"><a href="/board">board</a> / {esc(t["subject"])}</div>
<h1>{esc(t["subject"])}</h1>
{_flash(msg, error)}
<div class="thread">{posts}</div>
{_post_form(cfg, f"/board/{t['id']}", thread=False, signin=signin)}
"""
    return layout(cfg, t["subject"], body, "/board", stats)


def about(cfg, stats: dict) -> str:
    body = f"""
<h1>About this vessel</h1>
<div class="panel">
<p><b>{esc(cfg.site_name)}</b> is a small computer with a Wi-Fi radio and some storage.
It is <b>not connected to the internet</b>. Everything you see here lives on the box
and is shared only with people within radio range: whoever is aboard.</p>
<ul>
<li>No accounts, no passwords, no tracking. Your address is used only to count who is aboard and to rate-limit posting.</li>
<li>Cargo you bring aboard can be taken by anyone nearby. The captain can throw cargo overboard.</li>
<li>Chat and board posts stay on the box until they age out or the captain removes them.</li>
</ul>
<p>Reach it any time while connected at <b>http://{esc(cfg.hostname)}/</b>. If your browser insists on
https, type the address with <b>http://</b> in front, or use the "sign in to network" prompt.</p>
</div>
<div class="panel">
<h2>Lineage</h2>
<p>PirateBox was created by David Darts in 2011 and maintained by Matthias Strubel and a
community until 2019. {esc(cfg.site_name)} is an independent reimplementation of the idea:
a portable, anonymous, offline place to share files and talk.</p>
</div>
"""
    return layout(cfg, "About", body, "/about", stats)


def error_page(cfg, code: int, text: str) -> str:
    body = f'<h1>{code}</h1><p>{esc(text)}</p><p><a href="/">Back to the deck</a></p>'
    return layout(cfg, str(code), body)


# ---------------------------------------------------------------- quarterdeck

def _btn(action: str, token: str, label: str, fields: dict, danger: bool = True, confirm: str = "") -> str:
    hidden = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in fields.items())
    onsubmit = f' onsubmit="return confirm({esc(repr(confirm))})"' if confirm else ""
    return (f'<form class="inline" method="post" action="{action}"{onsubmit}>'
            f'<input type="hidden" name="token" value="{esc(token)}">{hidden}'
            f'<button class="tiny{" danger" if danger else ""}" type="submit">{esc(label)}</button></form>')


def quarterdeck(cfg, info: dict, cargo: list, chat: list, threads: list, token: str,
                stats: dict, msg: str = "", error: bool = False) -> str:
    disk = info["disk"]
    stat_tiles = [
        (stats["online"], "aboard now"),
        (stats["total"], "have come aboard"),
        (info["cargo_count"], "cargo items"),
        (human_size(info["cargo_bytes"]), "cargo stowed"),
        (human_size(disk["free"]), f"free of {human_size(disk['total'])}"),
        (info["chat_count"], "chat messages"),
        (f'{info["threads"]}/{info["posts"]}', "threads / posts"),
        (info["uptime"], "underway"),
    ]
    tiles = "".join(f'<div class="stat"><b>{esc(v)}</b><span>{esc(l)}</span></div>' for v, l in stat_tiles)

    def chk(name, on):
        return f'<label><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}>{name.replace("_enabled", "")}</label>'

    settings = f"""
<div class="panel">
<h2>Ship's articles</h2>
<form method="post" action="/quarterdeck/settings">
<input type="hidden" name="token" value="{esc(token)}">
<div class="toggles">{chk("uploads_enabled", cfg.uploads_enabled)}{chk("chat_enabled", cfg.chat_enabled)}{chk("board_enabled", cfg.board_enabled)}</div>
<label>Ship's name</label><input type="text" name="site_name" maxlength="40" value="{esc(cfg.site_name)}">
<label>Message of the day</label><textarea name="motd" maxlength="2000">{esc(cfg.motd)}</textarea>
<button type="submit">Save</button>
<span class="dim small">Saved to the data dir and kept across restarts; overrides parleybox.conf.</span>
</form>
</div>
"""
    rows = []
    for e in cargo:
        rel = e["rel"]
        rows.append(
            f'<tr><td><a href="/cargo/{quote(rel)}">{esc(rel)}</a></td>'
            f'<td class="size">{human_size(e["size"])}</td><td class="time">{fmt_time(e["mtime"])}</td>'
            f'<td class="act">{_btn("/quarterdeck/cargo/delete", token, "overboard", {"path": rel}, confirm=f"Throw {rel} overboard?")}</td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="4" class="dim">The hold is empty.</td></tr>')
    cargo_html = f"""
<div class="panel">
<h2>Cargo</h2>
<table class="files"><tr><th>Path</th><th>Size</th><th class="time">Stowed</th><th></th></tr>{"".join(rows)}</table>
{'<p class="dim small">Showing the newest %d items.</p>' % len(cargo) if info.get("cargo_truncated") else ""}
</div>
"""
    chat_rows = "".join(
        f'<p class="msg"><span class="t">{fmt_time(m["ts"])}</span><span class="n">{esc(m.get("name") or "anon")}</span>'
        f'<span class="b">{esc(m["text"])}</span> {_btn("/quarterdeck/chat/delete", token, "x", {"id": str(m["id"])})}</p>'
        for m in reversed(chat)
    ) or '<p class="dim">Quiet on deck.</p>'
    chat_html = f"""
<div class="panel">
<h2>Chat</h2>
{chat_rows}
<p>{_btn("/quarterdeck/chat/clear", token, "Clear the whole log", {}, confirm="Wipe every chat message?")}</p>
</div>
"""
    thread_rows = []
    for t in threads:
        op = t["posts"][0]
        thread_rows.append(
            f'<div class="thread"><a class="subject" href="/board/{t["id"]}">{esc(t["subject"])}</a> '
            f'<span class="dim small">{len(t["posts"]) - 1} replies &middot; {fmt_time(op["ts"])}</span> '
            f'{_btn("/quarterdeck/board/delete", token, "delete thread", {"thread": str(t["id"]), "post": str(op["id"])}, confirm="Delete this thread?")}'
            + "".join(
                f'<div class="small dim">&nbsp;&nbsp;No.{p["id"]} {esc(p.get("name") or "Anonymous")}: {esc(p["text"][:80])} '
                f'{_btn("/quarterdeck/board/delete", token, "x", {"thread": str(t["id"]), "post": str(p["id"])})}</div>'
                for p in t["posts"][1:]
            )
            + "</div>"
        )
    board_html = f"""
<div class="panel">
<h2>Board</h2>
{"".join(thread_rows) or '<p class="dim">No threads.</p>'}
</div>
"""
    body = f"""
<h1>Quarterdeck</h1>
<p class="dim small">Captain's station. Everything here is destructive and immediate.</p>
{_flash(msg, error)}
<div class="qd-stats">{tiles}</div>
{settings}
{cargo_html}
{chat_html}
{board_html}
"""
    return layout(cfg, "Quarterdeck", body, "/quarterdeck", stats)
