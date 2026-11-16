"""ui_common.py - potongan form yang dipakai bersama oleh form foreman dan form QC."""
import streamlit as st


def sku_picker(skus, key, default_sku=None):
    """Dropdown Tipe > Warna > Size. default_sku (sku_id) mengisi ketiga dropdown di awal, masih bisa diubah."""
    if default_sku is not None and f"{key}_m" not in st.session_state:
        hit = skus[skus.sku_id == default_sku]
        if not hit.empty:
            r = hit.iloc[0]
            st.session_state[f"{key}_m"] = f"{r.model_name} ({r.model_code})"
            st.session_state[f"{key}_c"] = f"{r.color_name} ({r.color_code})"
            st.session_state[f"{key}_s"] = f"{r.size_label} ({r.variant})"
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


def shift_doors(sb, machine_id, report_date, shift):
    """SKU dan status idle tiap pintu menurut laporan terbaru shift itu (awal, lalu perubahan kalau ada).
    Utama: fungsi database shift_door_skus (06_auto_isi_sku_akhir_shift.sql), jalan juga saat 03 membatasi baca.
    Cadangan: baca tabel langsung (hanya jalan kalau 03 belum dipasang). {} kalau dua-duanya gagal: SKU dipilih manual."""
    try:
        rows = sb.rpc("shift_door_skus", {"p_machine_id": machine_id, "p_report_date": str(report_date),
                                          "p_shift": shift}).execute().data
        return {r["door_no"]: r for r in rows}
    except Exception:
        pass
    try:
        reps = sb.table("setting_reports").select("id").eq("machine_id", machine_id) \
            .eq("report_date", str(report_date)).eq("shift", shift) \
            .in_("report_type", ["awal", "perubahan"]).order("id").execute().data
        ids = [r["id"] for r in reps]
        if not ids:
            return {}
        rows = sb.table("door_mould_temps").select("report_id, door_no, idle, sku_id") \
            .in_("report_id", ids).order("report_id").execute().data
        out = {}
        for r in rows:                      # laporan yang lebih baru menimpa yang lama
            out[r["door_no"]] = r
        return out
    except Exception:
        return {}


def sku_label(skus, sku_id):
    hit = skus[skus.sku_id == sku_id]
    if hit.empty:
        return f"SKU #{sku_id}"
    r = hit.iloc[0]
    return f"{r.model_name} {r.color_name} {r.size_label} ({r.variant})"
