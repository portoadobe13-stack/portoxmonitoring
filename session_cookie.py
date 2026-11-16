"""
session_cookie.py - ingat login setelah refresh halaman (tanpa library tambahan).

Streamlit membuat sesi baru tiap halaman di-refresh, jadi st.session_state ikut kosong. Solusinya: simpan
REFRESH TOKEN Supabase (bukan password) di cookie browser, lalu pulihkan login dari situ.
  - tulis cookie  : lewat JavaScript kecil (components.html)
  - baca cookie   : st.context.cookies (bawaan Streamlit >= 1.37; isinya cookie saat halaman dibuka)
Jangan pakai st.cache_data / st.cache_resource untuk login: cache dipakai bersama SEMUA pengguna,
jadi satu orang login = semua orang ikut login sebagai dia.
"""
import json
import tempfile
from pathlib import Path
from urllib.parse import unquote

import streamlit as st
import streamlit.components.v1 as components

COOKIE = "px_rt"
DAYS = 30


def _run_js(code):
    components.html(f"<script>{code}</script>", height=0)


def read_token():
    try:
        v = st.context.cookies.get(COOKIE)
    except Exception:
        return None
    return unquote(v) if v else None


def remember(token):
    t = json.dumps(token)
    _run_js("var p=window.parent, d=p.document;"
            f"d.cookie={json.dumps(COOKIE + '=')}+encodeURIComponent({t})+';path=/;max-age={DAYS * 86400};SameSite=Lax'"
            "+(p.location.protocol==='https:'?';Secure':'');"
            f"try{{p.localStorage.setItem({json.dumps(COOKIE)},{t});}}catch(e){{}}")


def forget():
    _run_js("var p=window.parent, d=p.document;"
            f"d.cookie={json.dumps(COOKIE)}+'=;path=/;max-age=0;SameSite=Lax';"
            f"try{{p.localStorage.removeItem({json.dumps(COOKIE)});}}catch(e){{}}")


def diagnose():
    """Tampilkan hasil pemeriksaan cookie (untuk mencari tahu kenapa login tidak bertahan setelah refresh)."""
    try:
        names = sorted(st.context.cookies.keys())
        server = f"Streamlit {st.__version__}. Cookie yang diterima server: {', '.join(names) or '(tidak ada)'}. " \
                 f"px_rt: {'ADA' if COOKIE in names else 'TIDAK ADA'}."
    except Exception as e:
        server = f"Streamlit {st.__version__}. st.context.cookies tidak tersedia ({e}). Perlu streamlit>=1.37."
    st.caption(server)
    components.html(
        "<pre id=o style='font:12px monospace;white-space:pre-wrap;margin:0'></pre><script>"
        "var out=[];try{var p=window.parent,d=p.document,s=p.location.protocol==='https:';"
        "out.push('alamat: '+p.location.href.split('?')[0]);"
        "out.push('cookie px_rt di browser: '+(d.cookie.indexOf('px_rt=')>=0?'ADA':'TIDAK ADA'));"
        "try{out.push('halaman ada di dalam iframe lain: '+(p!==window.top?'YA':'tidak'));}catch(e){out.push('halaman ada di dalam iframe lain: YA (lintas domain)');}"
        "d.cookie='px_test=1;path=/;max-age=60;SameSite=Lax'+(s?';Secure':'');"
        "out.push('tes tulis cookie: '+(d.cookie.indexOf('px_test=')>=0?'BERHASIL':'DIBLOKIR'));}"
        "catch(e){out.push('GAGAL mengakses halaman induk: '+e.message);}"
        "document.getElementById('o').innerText=out.join('\\n');</script>", height=110)


# ----------------------------------------------------------------------
# Pembaca token di sisi BROWSER. Dipakai karena di beberapa hosting (mis. Streamlit Community Cloud)
# server tidak menerima cookie lewat st.context.cookies. Komponen kecil ini membaca cookie/localStorage
# di browser dan mengirim nilainya ke Python.
# ----------------------------------------------------------------------
_READER_HTML = """<!doctype html><html><body><script>
var sent=false;
function send(t,d){window.parent.postMessage(Object.assign({isStreamlitMessage:true,type:t},d||{}),"*");}
function readTok(){var t="";try{var p=window.parent;
  var m=p.document.cookie.match(/(?:^|; )px_rt=([^;]*)/); if(m) t=decodeURIComponent(m[1]);
  if(!t){try{t=p.localStorage.getItem("px_rt")||"";}catch(e){}}}catch(e){}return t;}
window.addEventListener("message",function(e){
  if(e.data&&e.data.type==="streamlit:render"&&!sent){sent=true;
    send("streamlit:setComponentValue",{value:{token:readTok()},dataType:"json"});}});
send("streamlit:componentReady",{apiVersion:1});
send("streamlit:setFrameHeight",{height:0});
</script></body></html>"""

_reader = None
try:
    _rdir = Path(tempfile.gettempdir()) / "porto_cookie_reader_v1"
    _rdir.mkdir(parents=True, exist_ok=True)
    (_rdir / "index.html").write_text(_READER_HTML, encoding="utf-8")
    _reader = components.declare_component("porto_cookie_reader_v1", path=str(_rdir))
except Exception:
    _reader = None


def browser_token():
    """None = browser belum menjawab (tunggu rerun berikutnya); '' = tidak ada token tersimpan; selain itu token."""
    if _reader is None:
        return ""
    val = _reader(key="px_cookie_reader", default=None)
    if not isinstance(val, dict):
        return None
    return val.get("token") or ""
