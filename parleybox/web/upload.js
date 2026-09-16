(function () {
  var form = document.getElementById('upload-form');
  if (!form) return;
  var bar = document.getElementById('upload-progress');
  var status = document.getElementById('upload-status');
  var btn = form.querySelector('button');
  form.addEventListener('submit', function (ev) {
    if (!window.FormData || !window.XMLHttpRequest) return; // plain form fallback
    ev.preventDefault();
    var files = form.querySelector('input[type=file]').files;
    if (!files || !files.length) { status.textContent = 'Pick a file first.'; return; }
    var xhr = new XMLHttpRequest();
    xhr.open('POST', form.action, true);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    bar.style.display = 'block';
    btn.disabled = true;
    xhr.upload.onprogress = function (e) {
      if (e.lengthComputable) {
        bar.max = e.total; bar.value = e.loaded;
        status.textContent = Math.round(e.loaded / e.total * 100) + '%';
      }
    };
    xhr.onload = function () {
      btn.disabled = false;
      if (xhr.status === 200) {
        try {
          var r = JSON.parse(xhr.responseText);
          status.textContent = 'Uploaded: ' + r.saved.join(', ');
        } catch (e) { status.textContent = 'Uploaded.'; }
        form.reset();
        setTimeout(function () { window.location.reload(); }, 1200);
      } else {
        status.textContent = 'Upload failed: ' + xhr.responseText;
      }
    };
    xhr.onerror = function () { btn.disabled = false; status.textContent = 'Upload failed (network).'; };
    xhr.send(new FormData(form));
  });
})();
