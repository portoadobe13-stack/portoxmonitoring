import streamlit as st
import pandas as pd
from datetime import date
import time
from supabase import create_client

try:
    from groq import Groq
except ImportError:
    Groq = None
try:
    import anthropic
except ImportError:
    anthropic = None

import ocr_core as oc
import materials as mt
import shift_result as sr
import session_cookie as sc
import page_qc
from ui_common import sku_picker, sku_label, shift_doors
from rear_camera import rear_camera_input

st.set_page_config(page_title="Porto X Factory System", page_icon="🩴",
                   layout="wide", initial_sidebar_state="expanded")

# secrets.toml: SUPABASE_URL, SUPABASE_KEY (anon/publishable key, BUKAN service_role),
#   Mesin OCR (isi salah satu atau keduanya): ANTHROPIC_API_KEY, GROQ_API_KEY
#   Opsional: OCR_PROVIDER ("claude" | "groq", default claude), GROQ_VISION_MODEL,
#             CLAUDE_FAST_MODEL, CLAUDE_STRONG_MODEL, MOBILE_CAMERA_ONLY
GROQ_MODEL = st.secrets.get("GROQ_VISION_MODEL", oc.DEFAULT_VISION_MODEL)
CLAUDE_FAST = st.secrets.get("CLAUDE_FAST_MODEL", oc.DEFAULT_CLAUDE_FAST)
CLAUDE_STRONG = st.secrets.get("CLAUDE_STRONG_MODEL", oc.DEFAULT_CLAUDE_STRONG)
OCR_DEFAULT = str(st.secrets.get("OCR_PROVIDER", "claude")).lower()
# Di HP: foto wajib dari kamera (tidak bisa pilih galeri). Di PC: pilih file. Set false di secrets untuk mematikan.
MOBILE_CAMERA_ONLY = bool(st.secrets.get("MOBILE_CAMERA_ONLY", True))

# Siapa boleh apa (role 'pending' tidak boleh apa-apa)
FORM_ROLES = ("foreman", "admin")
DASHBOARD_ROLES = ("admin",)
OCR_CHOOSER_ROLES = FORM_ROLES      # siapa yang boleh ganti mesin OCR; isi ("admin",) untuk mengunci


def is_mobile():
    """Deteksi HP dari User-Agent (perkiraan; iPad mode desktop dianggap PC)."""
    try:
        ua = (st.context.headers.get("User-Agent") or "").lower()
    except Exception:
        return False
    return any(t in ua for t in ("android", "iphone", "ipod", "mobile"))


@st.cache_resource
def get_groq():
    return Groq(api_key=st.secrets["GROQ_API_KEY"])


@st.cache_resource
def get_claude():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"], timeout=60.0)


def get_client(provider):
    return get_claude() if provider == "claude" else get_groq()


def available_engines():
    """label -> (provider, model). Hanya yang API key-nya diisi dan library-nya terpasang."""
    eng = {}
    if anthropic is not None and "ANTHROPIC_API_KEY" in st.secrets:
        eng[f"Claude · {CLAUDE_FAST}"] = ("claude", CLAUDE_FAST)
        eng[f"Claude · {CLAUDE_STRONG}"] = ("claude", CLAUDE_STRONG)
    if Groq is not None and "GROQ_API_KEY" in st.secrets:
        eng[f"Groq · {GROQ_MODEL}"] = ("groq", GROQ_MODEL)
    return eng


# ----------------------------------------------------------------------
# Login (schema memakai RLS: tanpa login, semua insert/select ditolak)
# Klien Supabase dibuat PER SESI supaya login satu orang tidak terbawa ke orang lain.
# ----------------------------------------------------------------------
def restore_login():
    """Pulihkan login dari cookie setelah halaman di-refresh. True kalau berhasil."""
    if st.session_state.get("_logged_out") or st.session_state.get("_restore_failed"):
        return False
    rt = sc.read_token()                 # cara cepat: cookie yang diterima server
    if not rt:
        rt = sc.browser_token()          # cadangan: dibaca dari browser (untuk hosting yang tidak meneruskan cookie)
    if not rt:
        return False                     # None = browser belum menjawab; skrip dijalankan ulang otomatis saat sudah
    try:
        sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
        res = sb.auth.refresh_session(rt)
        prof = sb.table("profiles").select("full_name, role").eq("id", res.user.id).single().execute().data
    except Exception:
        st.session_state["_restore_failed"] = True     # cookie basi/dicabut: jangan dicoba lagi di sesi ini
        sc.forget()
        return False
    st.session_state.sb, st.session_state.profile = sb, prof
    return True


def keep_cookie_fresh(sb):
    """Refresh token Supabase berganti tiap diperbarui; simpan yang terbaru ke cookie.
    get_session() sekaligus memperbarui access token yang kedaluwarsa (1 jam).
    Return False kalau sesi sudah tidak berlaku (mis. dicabut), supaya foreman diminta login lagi."""
    try:
        sess = sb.auth.get_session()
    except Exception:
        sess = None
    if sess is None:
        return False
    if sess.refresh_token != st.session_state.get("_rt_written"):
        sc.remember(sess.refresh_token)
        st.session_state["_rt_written"] = sess.refresh_token
    return True


def login_sidebar():
    with st.sidebar:
        st.title("🩴 Porto X Production")
        if "sb" not in st.session_state:
            restore_login()
        if "sb" in st.session_state:
            prof = st.session_state.profile
            if not keep_cookie_fresh(st.session_state.sb):
                st.session_state.clear()
                st.session_state["_logged_out"] = True
                st.rerun()
            st.caption(f"👤 {prof.get('full_name') or '-'}  ·  role: **{prof['role']}**")
            with st.expander("🔧 Diagnosa login"):
                sc.diagnose()
            if st.button("Keluar"):
                try:
                    st.session_state.sb.auth.sign_out({"scope": "local"})   # hanya perangkat ini
                except Exception:
                    pass
                st.session_state.clear()
                st.session_state["_logged_out"] = True
                st.rerun()
            return True
        if st.session_state.get("_logged_out"):
            sc.forget()                                  # hapus cookie ingat-login di browser ini
        with st.form("login"):
            email = st.text_input("Email")
            pw = st.text_input("Password", type="password")
            if st.form_submit_button("Masuk"):
                try:
                    sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                    res = sb.auth.sign_in_with_password({"email": email, "password": pw})
                    prof = sb.table("profiles").select("full_name, role").eq("id", res.user.id).single().execute().data
                    st.session_state.clear()             # buang penanda lama (_logged_out, _restore_failed)
                    st.session_state.sb, st.session_state.profile = sb, prof
                    st.rerun()
                except Exception as e:
                    st.error(f"Login gagal: {e}")
        return False


def photo_input(label, key, max_side=1600):
    """Foto dari kamera belakang (HP) atau pilih file (PC). Return (file, id, pakai_kamera)."""
    use_cam = MOBILE_CAMERA_ONLY and is_mobile()
    if use_cam:
        up, fid = rear_camera_input(label, key=key, max_side=max_side)
    else:
        up = st.file_uploader(label, type=["jpg", "jpeg", "png"], key=key)
        fid = (getattr(up, "file_id", None) or (up.name, up.size)) if up else None
    return up, fid, use_cam


def pick_engine_bs():
    """Mesin OCR untuk lembar BS tulisan tangan: default Claude Haiku (murah). Admin boleh mengganti ke Sonnet lewat sidebar."""
    engines = available_engines()
    if not engines:
        return None
    labels = list(engines)
    fast = [l for l in labels if engines[l] == ("claude", CLAUDE_FAST)]
    default = fast[0] if fast else labels[0]
    if st.session_state.profile["role"] == "admin" and len(labels) > 1:
        default = st.sidebar.selectbox("🔎 Mesin OCR (BS)", labels, index=labels.index(default), key="bs_ocr_engine")
    return (default, *engines[default])


def load_master(sb):
    if "master" in st.session_state:
        return st.session_state.master
    machines = sb.table("machines").select("id, code").eq("active", True).order("code").execute().data
    skus = sb.table("v_sku_pick").select(
        "sku_id, sku_code, model_code, model_name, color_code, color_name, size_label, variant"
    ).execute().data
    db_keys = {r["key"] for r in sb.table("param_defs").select("key").execute().data}
    st.session_state.master = {
        "machines": {m["code"]: m["id"] for m in machines},
        "skus": pd.DataFrame(skus),
        "missing_keys": sorted(set(oc.PARAMS) - db_keys),
    }
    return st.session_state.master


# ----------------------------------------------------------------------
# Slot foto -> scan -> verifikasi
# ----------------------------------------------------------------------
def col_config(frames_name, df):
    cfg = {}
    for col in df.columns:
        if col == "Supplementary pressure":
            cfg[col] = st.column_config.SelectboxColumn(col, options=["ON", "OFF"])
        else:
            cfg[col] = st.column_config.NumberColumn(col)
    return cfg


def render_slot(gen, machine, page, number, engine):
    meta = oc.PAGE_META[page]
    sk = f"{machine}|{page}"
    slot = st.session_state.slots.setdefault(sk, {"ocr": None, "scan_no": 0, "verified": None})
    icon = "✅" if slot["verified"] is not None else ("🔍" if slot["ocr"] is not None else "⬜")

    with st.expander(f"{icon} {number}. {meta['title']}", expanded=slot["verified"] is None):
        label = f"Foto layar: {meta['screen']}"
        use_cam = MOBILE_CAMERA_ONLY and is_mobile()
        if use_cam:
            up, fid = rear_camera_input(label, key=f"cam_{gen}_{sk}")   # kamera belakang
        else:
            up = st.file_uploader(label, type=["jpg", "jpeg", "png"], key=f"up_{gen}_{sk}")
            fid = (getattr(up, "file_id", None) or (up.name, up.size)) if up else None
        if up:
            # Foto hanya diproses SEKALI per file (skrip Streamlit dijalankan ulang di tiap klik)
            cached = slot.get("img")
            if cached is None or cached[0] != fid:
                full = oc.prepare_image(up)
                thumb = full.copy()
                thumb.thumbnail((640, 640))
                cached = slot["img"] = (fid, full, thumb)
            img = cached[1]
            if not use_cam:
                st.image(cached[2], width=320)
            if st.button("⚡ Scan foto ini", key=f"scan_{gen}_{sk}"):
                if engine is None:
                    st.error("Mesin OCR belum diatur. Isi ANTHROPIC_API_KEY dan/atau GROQ_API_KEY di secrets.")
                else:
                    elabel, provider, model = engine
                    with st.spinner(f"{elabel} sedang membaca foto..."):
                        t0 = time.perf_counter()
                        values, err, usage = oc.scan_image(provider, get_client(provider), model, page, img)
                        secs = time.perf_counter() - t0
                    if err:
                        st.error(f"❌ {err}")
                    else:
                        slot.update(ocr=values, scan_no=slot["scan_no"] + 1, verified=None,
                                    info={"engine": elabel, "model": model, "secs": secs, "usage": usage})
        elif slot["ocr"] is None:
            if st.button("✍️ Isi manual (tanpa foto)", key=f"manual_{gen}_{sk}"):
                slot.update(ocr={}, scan_no=slot["scan_no"] + 1, verified=None, info=None)
                st.rerun()

        if slot["ocr"] is None:
            return

        filled = oc.count_filled(slot["ocr"])
        total = oc.expected_cells(page)
        st.caption(f"Terbaca {filled} dari {total} sel. **Cocokkan dengan foto, koreksi langsung di tabel, "
                   f"lalu klik Verifikasi.**")
        info = slot.get("info")
        if info:
            bits = [f"Dibaca oleh **{info['engine']}** dalam **{info['secs']:.1f} dtk**"]
            use = info.get("usage")
            if use and use.get("in") is not None:
                bits.append(f"{use['in']} token masuk / {use['out']} keluar")
            cost = oc.estimate_cost(info["model"], use)
            if cost is not None:
                bits.append(f"≈ ${cost:.4f} (perkiraan)")
            st.caption(" · ".join(bits))

        frames = oc.make_frames(page, slot["ocr"])
        edited = {}
        for name, df in frames.items():
            edited[name] = st.data_editor(df, key=f"ed_{gen}_{sk}_{name}_{slot['scan_no']}",
                                          column_config=col_config(name, df))
        current = oc.frames_to_values(page, edited)

        for w in oc.range_warnings(current):
            st.warning(f"Di luar rentang: {w}. Cek foto; kalau memang begitu di layar, tetap bisa disimpan "
                       f"(akan ditandai out_of_range).")
        empty = sum(v is None for v in current.values())
        if empty:
            st.info(f"{empty} sel kosong tidak akan disimpan.")

        if slot["verified"] is not None and current != slot["verified"]:
            st.warning("Ada perubahan setelah verifikasi. Klik Verifikasi lagi.")
        if st.button("✅ Verifikasi", key=f"ver_{gen}_{sk}", type="primary"):
            slot["verified"] = current
            st.rerun()
        if slot["verified"] is not None and current == slot["verified"]:
            st.success("Terverifikasi, akan ikut disimpan.")


# ----------------------------------------------------------------------
# Suhu cetakan per pintu (input manual foreman, tabel door_mould_temps)
# ----------------------------------------------------------------------
def render_doors(gen, machine, skus, report_type):
    """Return (include, doors_list, errors). Laporan awal: section wajib + Vacuum check tiap pintu."""
    awal = report_type == "awal"
    title = ("🌡️ 6. Suhu Cetakan & Vacuum Check per Pintu (wajib)" if awal
             else "🌡️ 6. Suhu Cetakan per Pintu (input manual)")
    with st.expander(title, expanded=awal):
        if awal:
            include = True
            st.caption("Laporan awal shift: suhu cetakan dan Vacuum check wajib diisi untuk setiap pintu.")
        else:
            include = st.checkbox("Sertakan suhu cetakan di laporan ini", value=report_type != "akhir", key=f"inc_{gen}_{machine}_{report_type}")
        doors, errors = [], []
        if not include:
            return False, doors, errors
        for d in oc.DOORS:
            k = f"door_{gen}_{machine}_{d}"
            st.markdown(f"**Pintu {d}**")
            idle = st.checkbox(f"Pintu {d} idle (tidak jalan)", key=f"{k}_idle")
            vac = None
            if awal:
                # sengaja tanpa nilai awal: foreman harus memilih sendiri tiap pintu
                vac = st.selectbox("Vacuum check", ["aktif", "nonaktif"], index=None,
                                   placeholder="Pilih status", key=f"{k}_vac")
                if vac is None:
                    errors.append(f"Pintu {d}: Vacuum check wajib dipilih")
            if idle:
                doors.append({"door_no": d, "idle": True, "vacuum_check": vac})
                st.divider()
                continue
            sku_id = sku_picker(skus, k)
            c1, c2, c3 = st.columns(3)
            side = c1.selectbox("Sisi aktif", ["kanan", "kiri"], key=f"{k}_side")
            svu = c2.number_input("SV atas (°C)", value=None, step=1.0, min_value=0.0, key=f"{k}_svu")
            svl = c3.number_input("SV bawah (°C)", value=None, step=1.0, min_value=0.0, key=f"{k}_svl")
            p1, p2 = st.columns(2)
            pvu_ok = p1.checkbox("PV atas sesuai (±3°C)", value=True, key=f"{k}_pvuok")
            pvu = None if pvu_ok else p1.number_input("PV atas aktual (°C)", value=None, key=f"{k}_pvu")
            pvl_ok = p2.checkbox("PV bawah sesuai (±3°C)", value=True, key=f"{k}_pvlok")
            pvl = None if pvl_ok else p2.number_input("PV bawah aktual (°C)", value=None, key=f"{k}_pvl")

            if sku_id is None:
                errors.append(f"Pintu {d}: SKU belum dipilih")
            if svu is None or svl is None:
                errors.append(f"Pintu {d}: SV atas dan bawah wajib diisi")
            if not pvu_ok and pvu is None:
                errors.append(f"Pintu {d}: isi PV atas atau centang 'sesuai'")
            if not pvl_ok and pvl is None:
                errors.append(f"Pintu {d}: isi PV bawah atau centang 'sesuai'")
            doors.append({"door_no": d, "idle": False, "sku_id": sku_id, "side": side,
                          "sv_upper": svu, "sv_lower": svl,
                          "pv_upper_ok": pvu_ok, "pv_upper": pvu,
                          "pv_lower_ok": pvl_ok, "pv_lower": pvl,
                          "vacuum_check": vac})
            st.divider()
    return True, doors, errors


# ----------------------------------------------------------------------
# Bahan per sak (tabel report_materials)
# ----------------------------------------------------------------------
def render_materials(gen, report_date):
    """Form bahan dinamis. Return list {"type","qty","date"} (divalidasi di mt.clean_rows saat simpan)."""
    rk, nk = f"matrows_{gen}", f"matnext_{gen}"
    if rk not in st.session_state:
        st.session_state[rk] = [{"id": 1, "type": "L"}, {"id": 2, "type": "S"}]
        st.session_state[nk] = 3
    st.markdown("**🧪 Bahan (per sak)**")
    st.caption("Isi jenis bahan, jumlah sak, dan tanggal bahan. Satu jenis boleh punya beberapa baris "
               "kalau tanggalnya berbeda. Baris yang dikosongkan diabaikan.")
    rows_state = st.session_state[rk]
    out = []
    for r in list(rows_state):
        c1, c2, c3 = st.columns([1, 1, 1.4])
        t = c1.selectbox("Bahan", list(mt.MATERIAL_TYPES), index=mt.MATERIAL_TYPES.index(r["type"]),
                         key=f"mat_t_{gen}_{r['id']}")
        q = c2.number_input("Jumlah (sak)", min_value=0.0, step=0.5, value=None, placeholder="mis. 3",
                            key=f"mat_q_{gen}_{r['id']}")
        d = c3.date_input("Tanggal bahan", value=None, format="DD/MM/YYYY", key=f"mat_d_{gen}_{r['id']}")
        if len(rows_state) > 1 and st.button("🗑 Hapus baris ini", key=f"mat_x_{gen}_{r['id']}"):
            rows_state.remove(r)
            st.rerun()
        out.append({"type": t, "qty": q, "date": d})
    if st.button("➕ Tambah baris bahan", key=f"mat_add_{gen}"):
        rows_state.append({"id": st.session_state[nk], "type": "L"})
        st.session_state[nk] += 1
        st.rerun()
    return out


# ----------------------------------------------------------------------
# Hasil produksi akhir shift PER PINTU
#   cycle count per pintu (dari HMI) + qty hasil per SKU (dibagi rata ke pintu yang SKU-nya sama)
# ----------------------------------------------------------------------
def render_results(gen, machine, machine_id, report_date, shift, skus, sb):
    """Return (payload, errors). payload = {"doors": [...], "skus": [...]} untuk RPC p_results."""
    st.markdown("**📦 Hasil Produksi Shift per Pintu**")
    st.caption("Diisi di akhir shift. Cycle count per pintu dibaca dari HMI mesin. Qty hasil diisi per SKU (size) "
               "untuk semua pintu yang memproduksi SKU itu; kalau ada dua pintu dengan SKU sama, qty-nya dibagi rata "
               "ke kedua pintu. Yield dihitung otomatis di database.")
    pk = f"shiftpref_{gen}_{machine}_{report_date}_{shift}"
    if pk not in st.session_state:
        st.session_state[pk] = shift_doors(sb, machine_id, report_date, shift)
    pref = st.session_state[pk]
    if pref:
        st.caption("SKU tiap pintu otomatis dari laporan awal/perubahan shift ini, tidak perlu dipilih lagi. "
                   "Centang \"SKU berbeda\" hanya kalau pintu itu ganti SKU di tengah shift tanpa laporan perubahan.")
    else:
        st.caption("Laporan awal shift ini tidak ditemukan, jadi SKU tiap pintu dipilih manual.")
    rows, errors = [], []
    for d in oc.DOORS:
        k = f"res_{gen}_{machine}_{d}"
        p = pref.get(d, {})
        st.markdown(f"**Pintu {d}**")
        idle = st.checkbox(f"Pintu {d} idle (tidak jalan)", value=bool(p.get("idle")), key=f"{k}_idle")
        if idle:
            st.divider()
            continue
        if p.get("sku_id") is not None:
            st.write(f"SKU: **{sku_label(skus, p['sku_id'])}**")
            if st.checkbox("SKU berbeda dari ini", key=f"{k}_chg"):
                sku_id = sku_picker(skus, k, p["sku_id"])
            else:
                sku_id = p["sku_id"]
        else:
            sku_id = sku_picker(skus, k)
        cyc = st.number_input("Cycle count (HMI)", min_value=0, step=1, value=None, placeholder="mis. 100",
                              key=f"{k}_cyc")
        if sku_id is None:
            errors.append(f"Pintu {d}: SKU belum dipilih (atau centang idle)")
        if cyc is None or cyc < 1:
            errors.append(f"Pintu {d}: cycle count wajib diisi (atau centang idle)")
        if sku_id is not None and cyc is not None and cyc >= 1:
            rows.append({"door_no": d, "sku_id": sku_id, "cycle_count": int(cyc)})
        st.divider()

    qty = {}
    groups = sr.group_doors(rows)
    if groups:
        st.markdown("**Qty hasil per SKU**")
        for sid, ds in groups.items():
            q = st.number_input(f"{sku_label(skus, sid)}  |  pintu {', '.join(str(x['door_no']) for x in ds)}",
                                min_value=0, step=1, value=None, placeholder="total qty SKU ini",
                                key=f"resq_{gen}_{machine}_{sid}")
            qty[sid] = int(q) if q is not None else None
    e2, warns = sr.validate_results(rows, qty)
    errors += [e for e in e2 if e not in errors]
    for w in warns:
        st.warning(w)

    prev = sr.preview(rows, qty)
    if prev and all(x["qty_door"] is not None for x in prev):
        st.dataframe(pd.DataFrame([{
            "Pintu": x["door_no"], "SKU": sku_label(skus, x["sku_id"]), "Cycle": x["cycle_count"],
            "Pintu dgn SKU sama": x["doors_same_sku"], "Qty pintu": x["qty_door"],
            "Yield pintu %": x["yield_door"], "Yield SKU %": x["yield_sku"]} for x in prev]), hide_index=True)
        tot = sr.shift_yield(rows, qty)
        if tot:
            st.info(f"Yield mesin: **{tot[2]:.1f}%** (qty {tot[1]} ÷ cycle {tot[0]})")
        if any(x["doors_same_sku"] > 1 for x in prev):
            st.caption("Pintu yang SKU-nya sama berbagi qty, jadi yield pintu hanya perkiraan. "
                       "Yield SKU lebih tepat untuk pintu-pintu itu.")
    payload = {"doors": rows, "skus": [{"sku_id": sid, "qty_hasil": q} for sid, q in qty.items() if q is not None]}
    return payload, errors


def schema_ready(sb):
    """Pastikan 05_vacuum_check_dan_hasil_per_pintu.sql sudah dijalankan; kalau belum, data vacuum dan
    hasil produksi akan diam-diam hilang (fungsi lama mengabaikan field yang tidak dikenal)."""
    if st.session_state.get("schema05"):
        return True
    try:
        sb.table("report_door_results").select("report_id").limit(1).execute()
        sb.table("report_sku_output").select("report_id").limit(1).execute()
        sb.table("door_mould_temps").select("vacuum_check").limit(1).execute()
    except Exception:
        return False
    st.session_state["schema05"] = True
    return True


# ----------------------------------------------------------------------
# Halaman: Form setting mesin
# ----------------------------------------------------------------------
def page_form(sb, master):
    if st.session_state.profile["role"] not in FORM_ROLES:
        st.warning("Role Anda tidak boleh mengisi laporan setting mesin.")
        return
    if master["missing_keys"]:
        st.error("Parameter ini belum ada di tabel param_defs (jalankan ulang 02_seed.sql): "
                 + ", ".join(master["missing_keys"]))
        return
    if "slots" not in st.session_state:
        st.session_state.slots, st.session_state.gen = {}, 0
    gen = st.session_state.gen

    st.header("📝 Laporan Setting Mesin EVA")
    c1, c2, c3 = st.columns(3)
    machine = c1.selectbox("Mesin", list(master["machines"]))
    report_date = c2.date_input("Tanggal produksi (tanggal shift mulai)", date.today())
    shift = c3.selectbox("Shift", ["pagi", "malam"])
    c4, c5, c6 = st.columns(3)
    team = c4.selectbox("Tim", ["A", "B", "C"])
    report_type = c5.selectbox("Jenis laporan", ["awal", "akhir", "perubahan"])
    change_reason = c6.selectbox("Alasan perubahan",
                                 ["ganti_bahan", "ganti_warna_tipe", "bs_tinggi", "trial", "lainnya"],
                                 disabled=report_type != "perubahan")
    mat_rows = render_materials(gen, report_date)
    notes = st.text_input("Catatan (opsional)")
    results, result_errors = {}, []
    if report_type == "akhir":
        results, result_errors = render_results(gen, machine, master["machines"][machine], report_date, shift,
                                                master["skus"], sb)

    st.markdown("---")
    st.subheader(f"📷 Scan layar HMI: {machine}")
    engines = available_engines()
    engine = None
    if engines:
        labels = list(engines)
        default_idx = next((i for i, l in enumerate(labels) if engines[l][0] == OCR_DEFAULT), 0)
        if st.session_state.profile["role"] in OCR_CHOOSER_ROLES and len(labels) > 1:
            elabel = st.sidebar.selectbox("🔎 Mesin OCR", labels, index=default_idx, key="ocr_engine")
        else:
            elabel = labels[default_idx]
        engine = (elabel, *engines[elabel])
        st.caption(f"Mesin OCR: **{elabel}**. Tiap slot: foto → Scan → cek & koreksi → Verifikasi.")
    else:
        st.error("Belum ada mesin OCR. Isi ANTHROPIC_API_KEY dan/atau GROQ_API_KEY di secrets "
                 "(dan pastikan paket `anthropic` / `groq` ada di requirements.txt). Anda tetap bisa Isi manual.")
    for n, page in enumerate(oc.PAGE_ORDER, start=1):
        render_slot(gen, machine, page, n, engine)
    include_doors, doors, door_errors = render_doors(gen, machine, master["skus"], report_type)

    st.markdown("---")
    st.subheader("💾 Simpan ke database")
    verified = {p: s["verified"] for k, s in st.session_state.slots.items()
                if k.startswith(machine + "|") and s["verified"] is not None
                for p in [k.split("|")[1]]}
    pending = [oc.PAGE_META[p]["title"] for p in oc.PAGE_ORDER if p not in verified]
    st.write("**Terverifikasi:** " + (", ".join(oc.PAGE_META[p]["title"] for p in oc.PAGE_ORDER if p in verified) or "belum ada"))
    if pending:
        st.write("**Belum diverifikasi (tidak ikut disimpan):** " + ", ".join(pending))

    if st.button("💾 Simpan Laporan Setting Mesin", type="primary"):
        if report_type in ("awal", "akhir") and not schema_ready(sb):
            st.error("Database belum diperbarui untuk Vacuum check dan hasil produksi per pintu. Jalankan "
                     "05_vacuum_check_dan_hasil_per_pintu.sql di Supabase → SQL Editor, lalu coba simpan lagi. "
                     "(Laporan belum disimpan.)")
            return
        errs = list(door_errors) if include_doors else []
        if report_type == "akhir":
            errs += result_errors
        if report_type != "akhir" and not verified and not (include_doors and doors):
            errs.append("Belum ada data yang diverifikasi.")
        if report_type == "perubahan" and not change_reason:
            errs.append("Alasan perubahan wajib untuk laporan 'perubahan'.")
        mat_payload, mat_errors = mt.clean_rows(mat_rows, report_date,
                                                required=report_type in ("awal", "perubahan"))
        errs += mat_errors
        if errs:
            st.error("Belum bisa disimpan:\n\n" + "\n".join(f"- {e}" for e in errs))
            return
        value_rows = []
        for p, vals in verified.items():
            ocr = st.session_state.slots[f"{machine}|{p}"]["ocr"]
            value_rows += oc.build_value_rows(vals, ocr)
        meta = {
            "machine_id": master["machines"][machine], "report_date": str(report_date), "shift": shift,
            "team": team, "report_type": report_type,
            "change_reason": change_reason if report_type == "perubahan" else None,
            "material_mix": mt.summary(mat_payload),     # ringkasan teks otomatis dari baris bahan
            "material_date": None,                        # tanggal per bahan ada di report_materials
            "notes": notes or None,
        }
        try:
            rid = sb.rpc("submit_setting_report", {
                "p_report": meta, "p_values": value_rows,
                "p_doors": doors if include_doors else [],
                "p_materials": mat_payload,
                "p_results": results if report_type == "akhir" else {},
            }).execute().data
            st.success(f"Laporan #{rid} tersimpan ({len(value_rows)} nilai setting"
                       f"{', ' + str(len(doors)) + ' pintu' if include_doors else ''}"
                       f"{', ' + str(len(mat_payload)) + ' baris bahan' if mat_payload else ''}"
                       f"{', hasil ' + str(len(results['doors'])) + ' pintu' if report_type == 'akhir' else ''}).")
            st.session_state.slots, st.session_state.gen = {}, gen + 1
            st.button("Buat laporan baru")
        except Exception as e:
            msg = str(e)
            if "p_materials" in msg or "report_materials" in msg and "does not exist" in msg:
                st.error("Database belum diperbarui untuk bahan per sak. Jalankan 04_bahan_per_sak.sql "
                         "di Supabase → SQL Editor, lalu coba simpan lagi.")
            elif "report_materials" in msg and "duplicate key" in msg:
                st.error("Ada bahan dengan jenis dan tanggal yang sama. Gabungkan jumlahnya jadi satu baris.")
            elif "setting_reports_awal_akhir_uq" in msg or "duplicate key" in msg:
                st.error(f"Laporan '{report_type}' untuk {machine} / {report_date} / shift {shift} sudah ada.")
            else:
                st.error(f"Gagal menyimpan: {msg}")


# ----------------------------------------------------------------------
# Halaman: Dashboard
# ----------------------------------------------------------------------
def page_dashboard(sb):
    if st.session_state.profile["role"] not in DASHBOARD_ROLES:
        st.warning("Role Anda tidak boleh membuka dashboard.")
        return
    st.header("📊 Dashboard Analisa Produksi Porto X")
    try:
        res = sb.table("setting_reports").select(
            "id, report_date, shift, team, report_type, change_reason, material_mix, created_at, machines(code)"
        ).order("created_at", desc=True).limit(30).execute().data
    except Exception as e:
        st.error(f"Gagal memuat: {e}")
        return
    if not res:
        st.info("Belum ada laporan.")
        return
    df = pd.DataFrame(res)
    df.insert(1, "mesin", df.pop("machines").map(lambda m: (m or {}).get("code")))
    st.dataframe(df)

    rid = st.selectbox("Lihat nilai setting laporan #", df["id"])
    vals = sb.table("setting_values").select(
        "param_key, idx, value_num, value_text, ocr_num, ocr_text, source, out_of_range, edited_from_ocr"
    ).eq("report_id", rid).order("param_key").order("idx").execute().data
    if vals:
        st.dataframe(pd.DataFrame(vals))
    try:
        mats = sb.table("report_materials").select("material_type, qty_sak, material_date") \
            .eq("report_id", rid).order("material_type").order("material_date").execute().data
        if mats:
            st.markdown("**Bahan laporan ini**")
            st.dataframe(pd.DataFrame(mats))
    except Exception:
        st.caption("Data bahan per sak belum tersedia (jalankan 04_bahan_per_sak.sql).")
    try:
        yr = sb.table("v_door_yield").select(
            "door_no, sku_code, cycle_count, doors_same_sku, qty_sku_total, qty_door, yield_door_pct, "
            "yield_sku_pct, shared_sku, sku_changed, sv_upper, sv_lower, vacuum_check"
        ).eq("report_id", rid).order("door_no").execute().data
        if yr:
            sy = sb.table("v_shift_yield").select("total_cycle, total_qty, yield_pct").eq("report_id", rid).execute().data
            st.markdown("**Hasil produksi per pintu (laporan akhir)**")
            if sy:
                st.metric("Yield mesin", f"{sy[0]['yield_pct']}%",
                          f"qty {sy[0]['total_qty']} ÷ cycle {sy[0]['total_cycle']}", delta_color="off")
            st.dataframe(pd.DataFrame(yr), hide_index=True)
            st.caption("sv_upper/sv_lower dan vacuum_check diambil dari laporan awal shift yang sama. "
                       "shared_sku = pintu berbagi SKU (pakai yield_sku_pct); sku_changed = SKU pintu berubah sejak awal shift.")
    except Exception:
        pass
    try:
        door_rows = sb.table("door_mould_temps").select("*").eq("report_id", rid).order("door_no").execute().data
        if door_rows:
            st.markdown("**Suhu cetakan & vacuum check per pintu**")
            st.dataframe(pd.DataFrame(door_rows).drop(columns=["report_id"], errors="ignore"))
    except Exception:
        pass


# ----------------------------------------------------------------------
if login_sidebar():
    sb = st.session_state.sb
    try:
        master = load_master(sb)
    except Exception as e:
        st.error(f"Gagal memuat master data (mesin/SKU). Cek role akun & RLS: {e}")
        st.stop()
    role = st.session_state.profile["role"]
    menus = []
    if role in FORM_ROLES:
        menus.append("Form Setting Mesin")
    if role in page_qc.QC_ROLES:
        menus += ["BS QC - Foto Lembar", "BS QC - Isi Langsung"]
    if role in DASHBOARD_ROLES:
        menus += ["Dashboard Analisa Manager", "Dashboard BS", "Master Kode BS"]
    if not menus:
        st.warning("Akun Anda belum punya akses ke menu apa pun (role: " + role + "). Hubungi admin.")
    else:
        menu = st.sidebar.selectbox("Pilih Menu", menus)
        if menu == "Form Setting Mesin":
            page_form(sb, master)
        elif menu == "BS QC - Foto Lembar":
            page_qc.page_bs_photo(sb, master, pick_engine_bs(), get_client, photo_input)
        elif menu == "BS QC - Isi Langsung":
            page_qc.page_bs_form(sb, master)
        elif menu == "Dashboard BS":
            page_qc.page_bs_dashboard(sb)
        elif menu == "Master Kode BS":
            page_qc.page_bs_codes(sb)
        else:
            page_dashboard(sb)
else:
    st.info("Silakan masuk lewat sidebar.")
