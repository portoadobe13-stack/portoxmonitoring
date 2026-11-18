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


def _group_slots(rows):
    """Baris slot -> {door_no: {"idle": bool, "sku_ids": [sku_id per slot, urut cetakan lalu size], "sku_id": slot pertama}}."""
    out = {}
    for r in rows:
        d = out.setdefault(r["door_no"], {"idle": bool(r.get("idle")), "sku_ids": []})
        if r.get("sku_id") is not None:
            d["sku_ids"].append(int(r["sku_id"]))
    for d in out.values():
        d["sku_id"] = d["sku_ids"][0] if d["sku_ids"] else None
    return out


def shift_doors(sb, machine_id, report_date, shift):
    """SKU tiap pintu menurut laporan terbaru shift itu (awal, lalu perubahan kalau ada).
    Satu pintu bisa punya 1 sampai 4 slot (2 cetakan x 2 size): hasilnya {door_no: {"idle", "sku_ids", "sku_id"}}.
    Utama: fungsi database shift_door_skus (06 dan 09), jalan juga saat 03 membatasi baca.
    Cadangan: baca tabel langsung (hanya jalan kalau 03 belum dipasang). {} kalau dua-duanya gagal: SKU dipilih manual."""
    try:
        rows = sb.rpc("shift_door_skus", {"p_machine_id": machine_id, "p_report_date": str(report_date),
                                          "p_shift": shift}).execute().data
        return _group_slots(rows)
    except Exception:
        pass
    try:
        reps = sb.table("setting_reports").select("id").eq("machine_id", machine_id) \
            .eq("report_date", str(report_date)).eq("shift", shift) \
            .in_("report_type", ["awal", "perubahan"]).order("id").execute().data
        ids = [r["id"] for r in reps]
        if not ids:
            return {}
        temps = sb.table("door_mould_temps").select("report_id, door_no, idle") \
            .in_("report_id", ids).order("report_id").execute().data
        latest = {}                         # laporan yang lebih baru menimpa yang lama
        for r in temps:
            latest[r["door_no"]] = (r["report_id"], bool(r.get("idle")))
        sk = sb.table("door_mould_skus").select("report_id, door_no, mould_no, size_no, sku_id") \
            .in_("report_id", ids).order("mould_no").order("size_no").execute().data
        rows = []
        for door, (rid, idle) in sorted(latest.items()):
            mine = [x for x in sk if x["report_id"] == rid and x["door_no"] == door]
            if idle or not mine:
                rows.append({"door_no": door, "idle": idle, "sku_id": None})
            rows += [{"door_no": door, "idle": False, "sku_id": x["sku_id"]} for x in mine]
        return _group_slots(rows)
    except Exception:
        return {}


def running_skus(pref):
    """{sku_id: [nomor pintu unik, urut]} untuk pintu yang tidak idle (dipakai form BS QC)."""
    out = {}
    for d in sorted(pref):
        r = pref[d]
        if r.get("idle"):
            continue
        for sid in r.get("sku_ids") or ([r["sku_id"]] if r.get("sku_id") is not None else []):
            if d not in out.setdefault(sid, []):
                out[sid].append(d)
    return out


def sku_label(skus, sku_id):
    hit = skus[skus.sku_id == sku_id]
    if hit.empty:
        return f"SKU #{sku_id}"
    r = hit.iloc[0]
    return f"{r.model_name} {r.color_name} {r.size_label} ({r.variant})"
