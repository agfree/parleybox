"""HTML rendering. Plain string building, no external template engine,
no external assets: everything must work with zero internet access."""

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
    ("/", "Home"),
    ("/files/", "Files"),
    ("/chat", "Chat"),
    ("/board", "Board"),
    ("/about", "About"),
]


def layout(cfg, title: str, body: str, active: str = "", stats: dict | None = None,
           scripts: tuple = ()) -> str:
    nav = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{label}</a>'
        for href, label in NAV
        if not (href == "/chat" and not cfg.chat_enabled)
        and not (href == "/board" and not cfg.board_enabled)
    )
    st = ""
    if stats:
        st = f'<span class="stats">{stats["online"]} aboard now &middot; {stats["total"]} visitors total</span>'
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
<footer>{esc(cfg.site_name)} &middot; offline &middot; anonymous &middot; no logs of who you are &middot; piratebox v{__version__}</footer>
</body>
</html>
"""


def _flash(msg: str, error: bool = False) -> str:
    if not msg:
        return ""
    return f'<div class="notice{" error" if error else ""}">{esc(msg)}</div>'


def home(cfg, stats: dict, msg: str = "") -> str:
    cards = [
        ("/files/", "Browse files", "Download what others have left here."),
    ]
    if cfg.uploads_enabled:
        cards.append(("/files/#upload", "Upload", f"Share something. Max {cfg.max_upload_mb} MB per upload."))
    if cfg.chat_enabled:
        cards.append(("/chat", "Chat", "Talk to whoever is connected right now."))
    if cfg.board_enabled:
        cards.append(("/board", "Message board", "Leave a note that outlives your visit."))
    cards.append(("/about", "About", "What this thing is and how it works."))
    grid = "".join(
        f'<a class="card" href="{href}"><b>{esc(t)}</b><span>{esc(d)}</span></a>' for href, t, d in cards
    )
    body = f"""
{_flash(msg)}
<pre class="flag">{esc(FLAG)}</pre>
<h1>Welcome to {esc(cfg.site_name)}</h1>
<pre class="motd">{esc(cfg.motd)}</pre>
<div class="grid">{grid}</div>
"""
    return layout(cfg, "Home", body, "/", stats)


def upload_form(cfg, msg: str = "", error: bool = False) -> str:
    if not cfg.uploads_enabled:
        return ""
    return f"""
<div class="panel" id="upload">
<h2>Upload</h2>
{_flash(msg, error)}
<form id="upload-form" method="post" action="/upload" enctype="multipart/form-data">
<label>Files (max {cfg.max_upload_mb} MB per upload)</label>
<input type="file" name="file" multiple required>
<button type="submit">Upload</button>
<progress id="upload-progress" value="0" max="100" style="display:none"></progress>
<div id="upload-status" class="dim small"></div>
</form>
<p class="dim small">Uploads land in <a href="/files/uploads/">/files/uploads/</a>. Anything you upload is visible to everyone on this network.</p>
</div>
"""


def file_listing(cfg, rel: str, entries: list, stats: dict, msg: str = "", error: bool = False) -> str:
    parts = [p for p in rel.split("/") if p]
    crumbs = ['<a href="/files/">files</a>']
    acc = ""
    for p in parts:
        acc += "/" + quote(p)
        crumbs.append(f'<a href="/files{acc}/">{esc(p)}</a>')
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
        rows.append('<tr><td colspan="3" class="dim">Nothing here yet.</td></tr>')
    body = f"""
<h1>Files</h1>
<div class="crumbs">{" / ".join(crumbs)}</div>
<table class="files">
<tr><th>Name</th><th>Size</th><th class="time">Modified</th></tr>
{"".join(rows)}
</table>
{upload_form(cfg, msg, error)}
"""
    return layout(cfg, "Files", body, "/files/", stats, scripts=("upload.js",))


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
<p class="dim small">Messages are kept until the box forgets them (last {cfg.chat_history}). Nobody knows who you are.</p>
"""
    return layout(cfg, "Chat", body, "/chat", stats, scripts=("chat.js",))


def _post_html(p: dict, op: bool = False) -> str:
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


def _post_form(cfg, action: str, thread: bool) -> str:
    subj = '<label>Subject</label><input type="text" name="subject" maxlength="100" required>' if thread else ""
    img = '<label>Image (optional, max 8 MB)</label><input type="file" name="image" accept="image/*">' if cfg.board_images else ""
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


def board_index(cfg, threads: list, stats: dict, msg: str = "", error: bool = False) -> str:
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
{_post_form(cfg, "/board", thread=True)}
"""
    return layout(cfg, "Board", body, "/board", stats)


def board_thread(cfg, t: dict, stats: dict, msg: str = "", error: bool = False) -> str:
    posts = "".join(_post_html(p, i == 0) for i, p in enumerate(t["posts"]))
    body = f"""
<div class="crumbs"><a href="/board">board</a> / {esc(t["subject"])}</div>
<h1>{esc(t["subject"])}</h1>
{_flash(msg, error)}
<div class="thread">{posts}</div>
{_post_form(cfg, f"/board/{t['id']}", thread=False)}
"""
    return layout(cfg, t["subject"], body, "/board", stats)


def about(cfg, stats: dict) -> str:
    body = f"""
<h1>About this box</h1>
<div class="panel">
<p><b>{esc(cfg.site_name)}</b> is a small computer with a Wi-Fi radio and some storage.
It is <b>not connected to the internet</b>. Everything you see here lives on the box
and is shared only with people within radio range.</p>
<ul>
<li>No accounts, no passwords, no tracking. Your IP address is used only to count visitors and rate-limit posting.</li>
<li>Files you upload can be downloaded by anyone nearby. Files can be deleted by whoever runs the box.</li>
<li>Chat and board posts are stored on the box until they age out.</li>
</ul>
<p>Reach it any time while connected at <b>http://{esc(cfg.hostname)}/</b>. If your browser insists on
https, type the address with <b>http://</b> in front, or use the "sign in to network" prompt.</p>
</div>
<div class="panel">
<h2>Lineage</h2>
<p>PirateBox was created by David Darts in 2011 and maintained by Matthias Strubel and a
community until 2019. This is an independent reimplementation of the idea:
a portable, anonymous, offline place to share files and talk.</p>
</div>
"""
    return layout(cfg, "About", body, "/about", stats)


def error_page(cfg, code: int, text: str) -> str:
    body = f'<h1>{code}</h1><p>{esc(text)}</p><p><a href="/">Back to shore</a></p>'
    return layout(cfg, str(code), body)
