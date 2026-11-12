import streamlit as st
import pandas as pd
from datetime import date
from supabase import create_client
from groq import Groq

import ocr_core as oc

st.set_page_config(page_title="Porto X Factory System", page_icon="🩴",
                   layout="wide", initial_sidebar_state="expanded")

# secrets.toml: SUPABASE_URL, SUPABASE_KEY (anon/publishable key, BUKAN service_role),
#               GROQ_API_KEY, (opsional) GROQ_VISION_MODEL
VISION_MODEL = st.secrets.get("GROQ_VISION_MODEL", oc.DEFAULT_VISION_MODEL)
# Di HP: foto wajib dari kamera (tidak bisa pilih galeri). Di PC: pilih file. Set false di secrets untuk mematikan.
MOBILE_CAMERA_ONLY = bool(st.secrets.get("MOBILE_CAMERA_ONLY", True))

# Siapa boleh apa (role 'pending' tidak boleh apa-apa)
FORM_ROLES = ("foreman", "admin")
DASHBOARD_ROLES = ("admin",)


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


# ----------------------------------------------------------------------
# Login (schema memakai RLS: tanpa login, semua insert/select ditolak)
# Klien Supabase dibuat PER SESI supaya login satu orang tidak terbawa ke orang lain.
# ----------------------------------------------------------------------
def login_sidebar():
    with st.sidebar:
        st.title("🩴 Porto X Production")
        if "sb" in st.session_state:
            prof = st.session_state.profile
            st.caption(f"👤 {prof.get('full_name') or '-'}  ·  role: **{prof['role']}**")
            if st.button("Keluar"):
                try:
                    st.session_state.sb.auth.sign_out()
                except Exception:
                    pass
                st.session_state.clear()
                st.rerun()
            return True
        with st.form("login"):
            email = st.text_input("Email")
            pw = st.text_input("Password", type="password")
            if st.form_submit_button("Masuk"):
                try:
                    sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                    res = sb.auth.sign_in_with_password({"email": email, "password": pw})
                    prof = sb.table("profiles").select("full_name, role").eq("id", res.user.id).single().execute().data
                    st.session_state.sb, st.session_state.profile = sb, prof
                    st.rerun()
                except Exception as e:
                    st.error(f"Login gagal: {e}")
        return False


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


def render_slot(gen, machine, page, number):
    meta = oc.PAGE_META[page]
    sk = f"{machine}|{page}"
    slot = st.session_state.slots.setdefault(sk, {"ocr": None, "scan_no": 0, "verified": None})
    icon = "✅" if slot["verified"] is not None else ("🔍" if slot["ocr"] is not None else "⬜")

    with st.expander(f"{icon} {number}. {meta['title']}", expanded=slot["verified"] is None):
        label = f"Foto layar: {meta['screen']}"
        if MOBILE_CAMERA_ONLY and is_mobile():
            up = st.camera_input(label, key=f"cam_{gen}_{sk}")
        else:
            up = st.file_uploader(label, type=["jpg", "jpeg", "png"], key=f"up_{gen}_{sk}")
        if up:
            img = oc.prepare_image(up)
            if not (MOBILE_CAMERA_ONLY and is_mobile()):
                st.image(img, width=320)
            if st.button("⚡ Scan foto ini", key=f"scan_{gen}_{sk}"):
                with st.spinner("Groq sedang membaca foto..."):
                    values, err = oc.scan_with_groq(get_groq(), VISION_MODEL, page, img)
                if err:
                    st.error(f"❌ {err}")
                else:
                    slot.update(ocr=values, scan_no=slot["scan_no"] + 1, verified=None)
        elif slot["ocr"] is None:
            if st.button("✍️ Isi manual (tanpa foto)", key=f"manual_{gen}_{sk}"):
                slot.update(ocr={}, scan_no=slot["scan_no"] + 1, verified=None)
                st.rerun()

        if slot["ocr"] is None:
            return

        filled = oc.count_filled(slot["ocr"])
        total = oc.expected_cells(page)
        st.caption(f"Terbaca {filled} dari {total} sel. **Cocokkan dengan foto, koreksi langsung di tabel, "
                   f"lalu klik Verifikasi.**")

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
def sku_picker(skus, key):
    c1, c2, c3 = st.columns(3)
    models = skus[["model_code", "model_name"]].drop_duplicates().sort_values("model_code")
    m_lab = {f"{r.model_name} ({r.model_code})": r.model_code for r in models.itertuples()}
    with c1:
        m = st.selectbox("Tipe", list(m_lab), index=None, placeholder="Pilih tipe", key=f"{key}_m")
    colors = skus[skus.model_code == m_lab[m]] if m else skus.iloc[0:0]
    col_df = colors[["color_code", "color_name"]].drop_duplicates().sort_values("color_name")
    c_lab = {f"{r.color_name} ({r.color_code})": r.color_code for r in col_df.itertuples()}
    with c2:
        c = st.selectbox("Warna", list(c_lab), index=None, placeholder="Pilih warna", key=f"{key}_c")
    sizes = colors[colors.color_code == c_lab[c]] if c else skus.iloc[0:0]
    s_lab = {f"{r.size_label} ({r.variant})": r.sku_id for r in sizes.itertuples()}
    with c3:
        s = st.selectbox("Size", list(s_lab), index=None, placeholder="Pilih size", key=f"{key}_s")
    return int(s_lab[s]) if s else None


def render_doors(gen, machine, skus):
    """Return (include, doors_list, errors)."""
    with st.expander("🌡️ 6. Suhu Cetakan per Pintu (input manual)", expanded=False):
        include = st.checkbox("Sertakan suhu cetakan di laporan ini", value=True, key=f"inc_{gen}_{machine}")
        doors, errors = [], []
        if not include:
            return False, doors, errors
        for d in oc.DOORS:
            k = f"door_{gen}_{machine}_{d}"
            st.markdown(f"**Pintu {d}**")
            if st.checkbox(f"Pintu {d} idle (tidak jalan)", key=f"{k}_idle"):
                doors.append({"door_no": d, "idle": True})
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
                          "pv_lower_ok": pvl_ok, "pv_lower": pvl})
            st.divider()
    return True, doors, errors


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
    c7, c8 = st.columns(2)
    material_mix = c7.text_input("Campuran bahan L dan S (mis. L60:S40)")
    material_date = c8.date_input("Tanggal bahan", value=None)
    notes = st.text_input("Catatan (opsional)")

    st.markdown("---")
    st.subheader(f"📷 Scan layar HMI: {machine}")
    st.caption(f"Model vision: `{VISION_MODEL}`. Tiap slot: upload foto → Scan → cek & koreksi → Verifikasi.")
    for n, page in enumerate(oc.PAGE_ORDER, start=1):
        render_slot(gen, machine, page, n)
    include_doors, doors, door_errors = render_doors(gen, machine, master["skus"])

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
        errs = list(door_errors) if include_doors else []
        if not verified and not (include_doors and doors):
            errs.append("Belum ada data yang diverifikasi.")
        if report_type == "perubahan" and not change_reason:
            errs.append("Alasan perubahan wajib untuk laporan 'perubahan'.")
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
            "material_mix": material_mix or None,
            "material_date": str(material_date) if material_date else None,
            "notes": notes or None,
        }
        try:
            rid = sb.rpc("submit_setting_report", {
                "p_report": meta, "p_values": value_rows,
                "p_doors": doors if include_doors else [],
            }).execute().data
            st.success(f"Laporan #{rid} tersimpan ({len(value_rows)} nilai setting"
                       f"{', ' + str(len(doors)) + ' pintu' if include_doors else ''}).")
            st.session_state.slots, st.session_state.gen = {}, gen + 1
            st.button("Buat laporan baru")
        except Exception as e:
            msg = str(e)
            if "setting_reports_awal_akhir_uq" in msg or "duplicate key" in msg:
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
    if role in DASHBOARD_ROLES:
        menus.append("Dashboard Analisa Manager")
    if not menus:
        st.warning("Akun Anda belum punya akses ke menu apa pun (role: " + role + "). Hubungi admin.")
    else:
        menu = st.sidebar.selectbox("Pilih Menu", menus)
        if menu == "Form Setting Mesin":
            page_form(sb, master)
        else:
            page_dashboard(sb)
else:
    st.info("Silakan masuk lewat sidebar.")
