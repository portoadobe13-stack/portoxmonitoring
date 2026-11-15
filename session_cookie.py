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
