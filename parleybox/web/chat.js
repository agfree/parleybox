(function () {
  var log = document.getElementById('chat-log');
  var form = document.getElementById('chat-form');
  var nameEl = document.getElementById('chat-name');
  var textEl = document.getElementById('chat-text');
  var lastId = parseInt(log.getAttribute('data-last-id') || '0', 10);
  var stopped = false;

  try { nameEl.value = localStorage.getItem('pb-name') || ''; } catch (e) {}

  function fmt(ts) {
    var d = new Date(ts * 1000);
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }
  function esc(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function append(m) {
    var atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    var p = document.createElement('p');
    p.className = 'msg';
    var n = m.name || 'anon';
    p.innerHTML = '<span class="t">' + fmt(m.ts) + '</span>' +
      '<span class="n' + (m.name ? '' : ' anon') + '">' + esc(n) + '</span>' +
      '<span class="b">' + esc(m.text) + '</span>';
    log.appendChild(p);
    if (m.id > lastId) lastId = m.id;
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
  function poll() {
    if (stopped) return;
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/api/chat?since=' + lastId + '&wait=25', true);
    xhr.timeout = 40000;
    xhr.onload = function () {
      if (xhr.status === 200) {
        try { JSON.parse(xhr.responseText).messages.forEach(append); } catch (e) {}
        poll();
      } else {
        setTimeout(poll, 3000);
      }
    };
    xhr.onerror = xhr.ontimeout = function () { setTimeout(poll, 3000); };
    xhr.send();
  }
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var text = textEl.value.trim();
    if (!text) return;
    try { localStorage.setItem('pb-name', nameEl.value); } catch (e) {}
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/chat', true);
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.onload = function () { if (xhr.status === 200) { textEl.value = ''; textEl.focus(); } };
    xhr.send('name=' + encodeURIComponent(nameEl.value) + '&text=' + encodeURIComponent(text));
  });
  log.scrollTop = log.scrollHeight;
  window.addEventListener('beforeunload', function () { stopped = true; });
  poll();
})();
