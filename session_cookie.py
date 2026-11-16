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
            "+(p.location.protocol==='https:'?';Secure':'');")


def forget():
    _run_js("var p=window.parent, d=p.document;"
            f"d.cookie={json.dumps(COOKIE)}+'=;path=/;max-age=0;SameSite=Lax';")


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
