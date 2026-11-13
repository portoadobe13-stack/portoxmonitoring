"""
rear_camera.py - input kamera untuk HP yang default-nya KAMERA BELAKANG.

st.camera_input bawaan Streamlit tidak bisa memilih kamera, jadi di HP selalu kamera depan.
Modul ini membuat komponen kecil (HTML+JS murni, tanpa library luar): tombol "Buka kamera"
-> pratinjau kamera belakang -> "Ambil foto" -> foto dikirim ke Python.
Kamera hanya menyala setelah tombol ditekan (5 slot tidak berebut kamera) dan dimatikan
setelah foto diambil. Wajib HTTPS (aturan browser). Kalau komponen gagal dimuat, otomatis
kembali ke st.camera_input.
"""
import base64
import io
import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{margin:0;font-family:system-ui,sans-serif;font-size:14px;color:#31333f}
  #label{margin:0 0 6px}
  #app{padding:0 0 2px}
  video,img{width:100%;max-height:340px;object-fit:contain;border-radius:8px;background:#000;display:none}
  .row{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
  button{flex:1;min-width:110px;padding:12px 8px;font-size:15px;border:1px solid #bbb;
         border-radius:8px;background:#fff;color:#31333f}
  button.primary{background:#ff4b4b;border-color:#ff4b4b;color:#fff}
  #msg{color:#b00020;margin-top:6px}
  #ver{color:#999;font-size:11px;margin-top:6px}
  [hidden]{display:none!important}
</style></head>
<body>
<div id="app">
<div id="label"></div>
<video id="v" playsinline muted autoplay></video>
<img id="still" alt="foto">
<div class="row">
  <button id="open" class="primary">📷 Buka kamera</button>
  <button id="snap" class="primary" hidden>📸 Ambil foto</button>
  <button id="flip" hidden>🔄 Ganti kamera</button>
  <button id="retake" hidden>↩ Ulangi foto</button>
</div>
<div id="msg"></div>
<div id="ver">kamera v3</div>
<canvas id="c" hidden></canvas>
</div>
<script>
const $ = id => document.getElementById(id);
const video = $("v"), still = $("still"), msg = $("msg");
let stream = null, facing = "environment";

function send(type, data) {
  window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*");
}
let lastH = -1;
function fit() {
  const h = Math.min(700, Math.ceil($("app").getBoundingClientRect().height) + 2);
  if (Math.abs(h - lastH) >= 2) { lastH = h; send("streamlit:setFrameHeight", {height: h}); }
}
if (window.ResizeObserver) new ResizeObserver(fit).observe($("app"));
still.onload = fit;
video.onloadedmetadata = fit;
function show(state) {            // state: idle | live | done
  video.style.display = state === "live" ? "block" : "none";
  still.style.display = state === "done" ? "block" : "none";
  $("open").hidden = state !== "idle";
  $("snap").hidden = state !== "live";
  $("flip").hidden = state !== "live";
  $("retake").hidden = state !== "done";
  fit();
}
function stopStream() {
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  video.srcObject = null;
}
async function start() {
  msg.textContent = "";
  stopStream();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {facingMode: {ideal: facing}, width: {ideal: 1920}, height: {ideal: 1080}},
      audio: false
    });
    video.srcObject = stream;
    await video.play();
    show("live");
  } catch (e) {
    show("idle");
    msg.textContent = "Kamera tidak bisa dibuka: " + (e && e.message ? e.message : e) +
      ". Pastikan izin kamera diberikan dan alamat aplikasi memakai HTTPS.";
    fit();
  }
}
$("open").onclick = start;
$("flip").onclick = () => { facing = facing === "environment" ? "user" : "environment"; start(); };
$("snap").onclick = () => {
  const w = video.videoWidth, h = video.videoHeight;
  if (!w || !h) { msg.textContent = "Kamera belum siap, coba lagi."; return; }
  const s = Math.min(1, 1600 / Math.max(w, h));
  const c = $("c");
  c.width = Math.round(w * s); c.height = Math.round(h * s);
  c.getContext("2d").drawImage(video, 0, 0, c.width, c.height);
  const url = c.toDataURL("image/jpeg", 0.9);
  stopStream();
  still.src = url;
  show("done");
  send("streamlit:setComponentValue", {value: {image: url, id: Date.now()}, dataType: "json"});
};
$("retake").onclick = () => {
  send("streamlit:setComponentValue", {value: null, dataType: "json"});
  start();
};
window.addEventListener("message", e => {
  if (e.data && e.data.type === "streamlit:render") {
    $("label").textContent = (e.data.args && e.data.args.label) || "";
    fit();
  }
});
send("streamlit:componentReady", {apiVersion: 1});
show("idle");
</script>
</body></html>
"""

_component = None
try:
    _dir = Path(tempfile.gettempdir()) / "porto_rear_camera_v3"
    _dir.mkdir(parents=True, exist_ok=True)
    (_dir / "index.html").write_text(_HTML, encoding="utf-8")
    _component = components.declare_component("porto_rear_camera_v3", path=str(_dir))
except Exception:
    _component = None


def rear_camera_input(label, key):
    """Return (file_like, foto_id) atau (None, None)."""
    if _component is None:
        up = st.camera_input(label, key=key)
        return (up, getattr(up, "file_id", None)) if up else (None, None)
    val = _component(label=label, key=key, default=None)
    if not val or not val.get("image"):
        return None, None
    try:
        raw = base64.b64decode(val["image"].split(",", 1)[1])
    except Exception:
        return None, None
    return io.BytesIO(raw), val.get("id")
