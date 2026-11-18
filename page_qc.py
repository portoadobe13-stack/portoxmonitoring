"""
page_qc.py - halaman sisi QC: form BS, dashboard BS (admin), master kode BS (admin).
Database: 07_qc_bs.sql. SKU tiap pintu dibaca dari fungsi shift_door_skus (06), sama seperti form akhir shift foreman.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import bs_form
import bs_logic as bl
import bs_ocr as bo
import ocr_core as oc
from ui_common import running_skus, shift_doors, sku_label, sku_picker

TZ = ZoneInfo("Asia/Jakarta")
QC_ROLES = ("qc", "admin")
ADMIN_ROLES = ("admin",)


def _codes(sb):
    """Kode BS aktif (dicache per sesi; dihapus saat admin menyimpan perubahan kode)."""
    if "bs_codes" not in st.session_state:
        st.session_state["bs_codes"] = sb.table("bs_codes").select("code, name, sort_order") \
            .eq("active", True).order("sort_order").execute().data
    return st.session_state["bs_codes"]


def _line_fields(base, codes):
    """TS + kode BS satu SKU. Return (ts, defects)."""
    ts = st.number_input("TS (tidak sepasang, pcs)", min_value=0, step=1, value=None, placeholder="0",
                         key=f"{base}_ts", help="Satu kaki BS, pasangannya bagus.")
    labels = {f"{c['code']} · {c['name']}": c["code"] for c in codes}
    picked = st.multiselect("Kode BS yang ditemukan", list(labels), key=f"{base}_codes",
                            placeholder="Pilih kode BS (kosongkan kalau tidak ada BS)")
    defects = []
    for lab in picked:
        q = st.number_input(f"{lab} (pcs)", min_value=0, step=1, value=None, placeholder="jumlah",
                            key=f"{base}_q_{labels[lab]}")
        defects.append({"code": labels[lab], "qty": int(q) if q is not None else None})
    return (int(ts) if ts is not None else None), defects


def page_bs_form(sb, master):
    if st.session_state.profile["role"] not in QC_ROLES:
        st.warning("Role Anda tidak boleh mengisi laporan BS.")
        return
    if "bsgen" not in st.session_state:
        st.session_state["bsgen"] = 0
    gen = st.session_state["bsgen"]
    codes = _codes(sb)
    if not codes:
        st.error("Belum ada kode BS aktif. Admin perlu mengaktifkan kode di menu Master Kode BS.")
        return
    skus = master["skus"]

    st.header("🔍 Laporan BS QC")
    sug = st.session_state.setdefault(f"bssug_{gen}", bl.suggest(datetime.now(TZ)))
    c1, c2 = st.columns(2)
    machine = c1.selectbox("Mesin", list(master["machines"]), key=f"bsm_{gen}")
    report_date = c2.date_input("Tanggal produksi (tanggal shift mulai)", sug[0], format="DD/MM/YYYY",
                                key=f"bsd_{gen}")
    c3, c4, c5 = st.columns(3)
    shift = c3.selectbox("Shift", list(bl.CHECKPOINTS), index=list(bl.CHECKPOINTS).index(sug[1]),
                         key=f"bss_{gen}")
    cps = bl.CHECKPOINTS[shift]
    checkpoint = c4.selectbox("Jam pemeriksaan", cps, index=cps.index(sug[2]) if sug[2] in cps else 0,
                              key=f"bsc_{gen}_{shift}")
    team = c5.selectbox("Tim", ["A", "B", "C"], key=f"bst_{gen}")
    st.caption("Jam 01:00 dan 05:00 termasuk shift malam yang dimulai di tanggal sebelumnya.")

    machine_id = master["machines"][machine]
    try:
        done = [r["checkpoint"] for r in sb.table("bs_reports").select("checkpoint").eq("machine_id", machine_id)
                .eq("report_date", str(report_date)).eq("shift", shift).execute().data]
    except Exception:
        done = []
    if done:
        st.caption("Sudah ada laporan BS untuk: " + ", ".join(sorted(done)))
    if checkpoint in done:
        st.warning(f"Pemeriksaan {checkpoint} untuk {machine} / shift {shift} sudah ada, jadi tidak bisa disimpan lagi.")

    pk = f"bsdoors_{gen}_{machine}_{report_date}_{shift}"
    if pk not in st.session_state:
        st.session_state[pk] = shift_doors(sb, machine_id, report_date, shift)
    pref = st.session_state[pk]
    running = running_skus(pref)

    base0 = f"bs_{gen}_{machine}_{report_date}_{shift}_{checkpoint}"
    st.markdown("---")
    if running:
        st.subheader("SKU yang sedang jalan")
        st.caption("Dari laporan foreman shift ini. BS diisi per SKU (size); kalau ada beberapa pintu dengan SKU "
                   "yang sama, BS dibagi rata ke pintu-pintu itu secara otomatis.")
    else:
        st.warning("Laporan foreman untuk mesin dan shift ini belum ditemukan. Tambahkan SKU yang diperiksa "
                   "secara manual di bawah.")

    rows = []
    for sid, doors in running.items():
        with st.container(border=True):
            label = sku_label(skus, sid)
            st.markdown(f"**{label}**  ·  pintu {', '.join(str(x) for x in doors)}")
            skip = st.checkbox("Tidak diperiksa pada pemeriksaan ini", key=f"{base0}_{sid}_skip")
            if skip:
                continue
            ts, defects = _line_fields(f"{base0}_{sid}", codes)
            rows.append({"sku_id": sid, "label": label, "ts": ts, "defects": defects})

    xk = f"bsx_{gen}"
    if xk not in st.session_state:
        st.session_state[xk] = [1] if not running else []
        st.session_state[xk + "_n"] = 2 if not running else 1
    st.subheader("SKU lain (di luar daftar)" if running else "SKU diperiksa")
    for xid in list(st.session_state[xk]):
        with st.container(border=True):
            sid = sku_picker(skus, f"{base0}_x{xid}")
            if sid is None:
                st.caption("Pilih tipe, warna, dan size.")
            else:
                ts, defects = _line_fields(f"{base0}_x{xid}_{sid}", codes)
                rows.append({"sku_id": sid, "label": sku_label(skus, sid), "ts": ts, "defects": defects})
            if st.button("🗑 Hapus SKU ini", key=f"{base0}_xdel{xid}"):
                st.session_state[xk].remove(xid)
                st.rerun()
    if st.button("➕ Tambah SKU lain", key=f"{base0}_xadd"):
        st.session_state[xk].append(st.session_state[xk + "_n"])
        st.session_state[xk + "_n"] += 1
        st.rerun()
    # baris SKU lain yang belum dipilih tidak dikirim (dan tidak dianggap error)
    rows = [r for r in rows if r["sku_id"] is not None]

    st.markdown("---")
    notes = st.text_input("Catatan (opsional)", key=f"{base0}_notes")
    payload, errors = bl.clean_lines(rows)
    if payload:
        bs, ts = bl.totals(payload)
        st.info(f"Ringkasan: {len(payload)} SKU, BS **{bs}** pcs, TS **{ts}** pcs")

    if st.button("💾 Simpan Laporan BS", type="primary", key=f"{base0}_save"):
        if checkpoint in done:
            errors.append(f"Pemeriksaan {checkpoint} sudah ada")
        if errors:
            st.error("Belum bisa disimpan:\n\n" + "\n".join(f"- {e}" for e in errors))
            return
        meta = {"machine_id": machine_id, "report_date": str(report_date), "shift": shift, "team": team,
                "checkpoint": checkpoint, "notes": notes or None}
        try:
            rid = sb.rpc("submit_bs_report", {"p_report": meta, "p_lines": payload}).execute().data
        except Exception as e:
            msg = str(e)
            if "submit_bs_report" in msg and ("does not exist" in msg or "Could not find" in msg):
                st.error("Database belum diperbarui untuk laporan BS. Jalankan 07_qc_bs.sql di Supabase → "
                         "SQL Editor, lalu coba simpan lagi.")
            elif "duplicate key" in msg and "bs_reports" in msg:
                st.error(f"Pemeriksaan {checkpoint} untuk {machine} / {report_date} / shift {shift} sudah ada.")
            elif "bs_defects_pkey" in msg:
                st.error("Ada kode BS yang terisi dua kali pada satu SKU.")
            else:
                st.error(f"Gagal menyimpan: {msg}")
            return
        bs, ts = bl.totals(payload)
        st.success(f"Laporan BS #{rid} tersimpan: {machine}, {checkpoint}, {len(payload)} SKU, "
                   f"BS {bs} pcs, TS {ts} pcs.")
        st.session_state["bsgen"] = gen + 1
        st.button("Buat laporan BS baru")


# ----------------------------------------------------------------------
# BS dari foto lembar tulisan tangan (satu lembar = satu mesin = satu shift)
# ----------------------------------------------------------------------
STATUS_ICON = {"exact": "✅", "fuzzy": "⚠️ periksa", "other": "🔶 bukan SKU yang sedang jalan", "none": "❌ tidak dikenali"}


def page_bs_photo(sb, master, engine, get_client, photo_input):
    """engine = (label, provider, model) atau None; get_client(provider); photo_input(label, key) -> (file, id, pakai_kamera)."""
    if st.session_state.profile["role"] not in QC_ROLES:
        st.warning("Role Anda tidak boleh mengisi laporan BS.")
        return
    gen = st.session_state.setdefault("bspgen", 0)
    codes = _codes(sb)
    if not codes:
        st.error("Belum ada kode BS aktif. Admin perlu mengaktifkan kode di menu Master Kode BS.")
        return
    skus = master["skus"]
    st.header("📷 Laporan BS QC dari Foto Lembar")
    if engine is None:
        st.error("Mesin OCR belum diatur. Isi ANTHROPIC_API_KEY dan/atau GROQ_API_KEY di secrets. "
                 "Sementara itu pakai menu 'BS QC - Isi Langsung'.")
        return
    elabel, provider, model = engine
    st.caption(f"Mesin OCR: **{elabel}**. Foto seluruh lembar: tegak, tanpa bayangan, empat sudut terlihat. "
               "Satu foto boleh dikirim lebih dari sekali (misalnya setelah tiap jam pemeriksaan); "
               "pemeriksaan yang sudah tersimpan tidak akan disimpan ulang.")
    state = st.session_state.setdefault(f"bsp_{gen}", {"parsed": None, "scan_no": 0})
    up, fid, use_cam = photo_input("Foto lembar QC", f"bsp_photo_{gen}", max_side=bo.MAX_SIDE)
    if up:
        cached = state.get("img")
        if cached is None or cached[0] != fid:
            full = oc.prepare_image(up, max_side=bo.MAX_SIDE)
            thumb = full.copy()
            thumb.thumbnail((640, 640))
            state["img"] = (fid, full, thumb)
            state["parsed"] = None
        if not use_cam:
            st.image(state["img"][2], width=360)
        if st.button("⚡ Baca lembar ini", key=f"bsp_scan_{gen}"):
            with st.spinner(f"{elabel} sedang membaca lembar (bisa 10-30 detik)..."):
                parsed, err, usage = bo.scan_form(provider, get_client(provider), model, state["img"][1], codes)
            if err:
                st.error(f"❌ {err}")
            else:
                state.update(parsed=parsed, scan_no=state["scan_no"] + 1, info={"engine": elabel, "usage": usage})
    parsed = state.get("parsed")
    if parsed is None:
        return
    n = state["scan_no"]
    use = state.get("info", {}).get("usage")
    cost = oc.estimate_cost(model, use) if use else None
    st.caption("Cocokkan dengan lembar, koreksi, lalu simpan. " + (f"≈ ${cost:.3f} per lembar (perkiraan)." if cost else ""))

    # --- 1. kepala lembar
    st.subheader("1. Kepala lembar")
    sug = st.session_state.setdefault(f"bspsug_{gen}", bl.suggest(datetime.now(TZ)))
    mnames = list(master["machines"])
    mm = bo.match_machine(parsed["header"]["mesin"], mnames)
    c1, c2, c3, c4 = st.columns(4)
    machine = c1.selectbox("Mesin", mnames, index=mnames.index(mm) if mm else None, placeholder="Pilih mesin",
                           key=f"bsp_m_{gen}_{n}")
    if not mm:
        c1.caption(f"Tertulis: '{parsed['header']['mesin'] or '-'}' (tidak dikenali)")
    report_date = c2.date_input("Tanggal produksi", bo.parse_date(parsed["header"]["tanggal"]) or sug[0],
                                format="DD/MM/YYYY", key=f"bsp_d_{gen}_{n}")
    shifts = list(bl.CHECKPOINTS)
    shift = c3.selectbox("Shift", shifts, index=shifts.index(parsed["header"]["shift"] or sug[1]), key=f"bsp_s_{gen}_{n}")
    team = c4.selectbox("Tim", ["A", "B", "C"], index=["A", "B", "C"].index(parsed["header"]["tim"])
                        if parsed["header"]["tim"] in ("A", "B", "C") else 0, key=f"bsp_t_{gen}_{n}")
    if machine is None:
        st.info("Pilih mesin untuk melanjutkan.")
        return
    machine_id = master["machines"][machine]
    ctx = f"{gen}_{n}_{machine}_{report_date}_{shift}"

    # --- 2. SKU
    st.subheader("2. SKU tiap baris")
    pk = f"bspdoors_{ctx}"
    if pk not in st.session_state:
        st.session_state[pk] = shift_doors(sb, machine_id, report_date, shift)
    pref = st.session_state[pk]
    run_ids = sorted(running_skus(pref))
    recs = skus.to_dict("records")
    all_c = [{"sku_id": int(r["sku_id"]), "model_code": r["model_code"], "color_code": r["color_code"],
              "size_label": r["size_label"]} for r in recs]
    run_c = [c for c in all_c if c["sku_id"] in run_ids]
    if not run_ids:
        st.warning("Laporan foreman untuk mesin dan shift ini belum ditemukan, jadi SKU dicocokkan ke seluruh daftar SKU.")
    rows_idx = bo.active_rows(parsed)
    mapping = {}
    for i in rows_idx:
        row = parsed["skus"][i]
        sid_auto, status = bo.match_row(row, run_c, all_c)
        key = f"bsp_sku_{ctx}_{i}"
        c1, c2 = st.columns([1, 2])
        c1.markdown(f"**S{i + 1}** tertulis:  \n`{row['tipe'] or '-'}` · `{row['warna'] or '-'}` · `{row['size'] or '-'}`")
        c1.caption(STATUS_ICON.get(status, ""))
        opts = run_ids + ["__lain__"]
        default = opts.index(sid_auto) if sid_auto in run_ids else (len(opts) - 1)
        with c2:
            pick = st.selectbox("SKU", opts, index=default, key=key, label_visibility="collapsed",
                                format_func=lambda v: "Pilih dari semua SKU…" if v == "__lain__" else sku_label(skus, v))
            if pick == "__lain__":
                pick = sku_picker(skus, f"{key}_p", default_sku=sid_auto if status == "other" else None)
        mapping[i] = pick
    for i in range(bo.SKU_ROWS):
        mapping.setdefault(i, None)

    # --- 3. pemeriksaan
    try:
        done = [r["checkpoint"] for r in sb.table("bs_reports").select("checkpoint").eq("machine_id", machine_id)
                .eq("report_date", str(report_date)).eq("shift", shift).execute().data]
    except Exception:
        done = []
    st.subheader("3. Pemeriksaan")
    st.caption("Sel kosong = 0. Sel yang tidak terbaca dibiarkan kosong di tabel: isi angkanya sebelum menyimpan.")
    labels = [c["code"] for c in codes]
    cols = ["TS"] + labels
    edited_blocks, selected = [], []
    for b in range(bo.WINDOWS):
        cp = bo.CHECKPOINTS[shift][b]
        blk = parsed["blocks"][b]
        filled = bo.block_filled(blk)
        with st.container(border=True):
            if cp in done:
                st.markdown(f"**Pemeriksaan {b + 1} · {cp}**  ·  ✅ sudah tersimpan")
                edited_blocks.append(blk["rows"])
                continue
            sel = st.checkbox(f"Simpan Pemeriksaan {b + 1} · {cp}" + ("" if filled else "  (kosong di lembar)"),
                              value=filled, key=f"bsp_sel_{ctx}_{b}")
            df = pd.DataFrame([[None if v is None else float(v) for v in blk["rows"][i]] for i in rows_idx],
                              index=[f"S{i + 1}" for i in rows_idx], columns=cols, dtype="float64")
            with st.expander("Lihat / koreksi angka", expanded=filled):
                ed = st.data_editor(df, key=f"bsp_ed_{gen}_{n}_{b}",
                                    column_config={c: st.column_config.NumberColumn(c, min_value=0, step=1, format="%d")
                                                   for c in cols})
            rows = [list(r) for r in blk["rows"]]
            for pos, i in enumerate(rows_idx):
                rows[i] = [None if pd.isna(v) else int(v) for v in ed.iloc[pos].tolist()]
            edited_blocks.append(rows)
            if sel:
                selected.append(b)

    # --- 4. simpan
    st.subheader("4. Simpan")
    notes = st.text_input("Catatan (opsional)", key=f"bsp_notes_{gen}_{n}")
    if st.button("💾 Simpan laporan BS", type="primary", key=f"bsp_save_{gen}_{n}"):
        if not selected:
            st.error("Tidak ada pemeriksaan yang dipilih untuk disimpan.")
            return
        edited = {"blocks": [{"rows": r} for r in edited_blocks]}
        reports, errs = bo.build_reports(edited, codes, mapping, selected, shift, saved_checkpoints=done)
        if errs:
            st.error("Belum bisa disimpan:\n\n" + "\n".join(f"- {e}" for e in errs))
            return
        ok, fail = [], []
        for r in reports:
            meta = {"machine_id": machine_id, "report_date": str(report_date), "shift": shift, "team": team,
                    "checkpoint": r["checkpoint"], "notes": notes or None}
            try:
                rid = sb.rpc("submit_bs_report", {"p_report": meta, "p_lines": r["lines"]}).execute().data
                ok.append(f"{r['checkpoint']} (#{rid}, {len(r['lines'])} SKU)")
            except Exception as e:
                msg = str(e)
                if "submit_bs_report" in msg and ("does not exist" in msg or "Could not find" in msg):
                    msg = "database belum diperbarui: jalankan 07_qc_bs.sql"
                elif "duplicate key" in msg and "bs_reports" in msg:
                    msg = "pemeriksaan ini sudah ada"
                fail.append(f"{r['checkpoint']}: {msg}")
        if ok:
            st.success("Tersimpan: " + ", ".join(ok))
        if fail:
            st.error("Gagal: " + "; ".join(fail))
        if ok and not fail:
            if st.button("Lembar berikutnya", key=f"bsp_next_{gen}_{n}"):
                st.session_state["bspgen"] = gen + 1
                st.rerun()


def page_bs_dashboard(sb):
    if st.session_state.profile["role"] not in ADMIN_ROLES:
        st.warning("Role Anda tidak boleh membuka dashboard.")
        return
    st.header("📊 Dashboard BS")
    rng = st.date_input("Periode", (date.today() - timedelta(days=6), date.today()), format="DD/MM/YYYY")
    d1, d2 = (rng[0], rng[-1]) if isinstance(rng, (tuple, list)) and rng else (rng, rng)
    try:
        shift_rows = sb.table("v_bs_shift_sku").select("*").gte("report_date", str(d1)).lte("report_date", str(d2)) \
            .order("report_date", desc=True).limit(3000).execute().data
    except Exception as e:
        st.error(f"Gagal memuat (sudah menjalankan 07_qc_bs.sql?): {e}")
        return
    if not shift_rows:
        st.info("Belum ada laporan BS pada periode ini.")
        return
    df = pd.DataFrame(shift_rows)
    machines = sorted(df["machine_code"].unique())
    pick = st.multiselect("Mesin", machines, default=machines)
    df = df[df["machine_code"].isin(pick)]
    if df.empty:
        st.info("Tidak ada data untuk mesin yang dipilih.")
        return

    with_qty = df[df["qty_hasil"].notna() & (df["qty_hasil"] > 0)]
    m1, m2, m3 = st.columns(3)
    m1.metric("Total BS (pcs)", int(df["bs_qty"].sum()))
    m2.metric("Total TS (pcs)", int(df["ts_qty"].sum()))
    if len(with_qty):
        m3.metric("BS ÷ qty hasil", f"{with_qty['bs_qty'].sum() * 100.0 / with_qty['qty_hasil'].sum():.2f}%",
                  help="Hanya shift yang sudah punya laporan akhir foreman.")
    st.markdown("**Per shift per SKU**")
    st.dataframe(df.drop(columns=["machine_id", "sku_id"], errors="ignore"), hide_index=True)

    try:
        det = pd.DataFrame(sb.table("v_bs_detail").select("report_date, machine_code, sku_code, bs_code, bs_name, qty")
                           .gte("report_date", str(d1)).lte("report_date", str(d2)).limit(20000).execute().data)
        det = det[det["bs_code"].notna() & det["machine_code"].isin(pick)] if len(det) else det
        if len(det):
            top = det.groupby(["bs_code", "bs_name"], as_index=False)["qty"].sum().sort_values("qty", ascending=False)
            top["kode"] = top["bs_code"] + " · " + top["bs_name"]
            st.markdown("**Kode BS terbanyak**")
            st.bar_chart(top.set_index("kode")["qty"], horizontal=True)
    except Exception:
        pass

    try:
        door = pd.DataFrame(sb.table("v_bs_door").select("machine_code, report_date, shift, door_no, sku_code, "
                                                          "bs_qty_door, ts_qty_door, shared_sku")
                            .gte("report_date", str(d1)).lte("report_date", str(d2)).limit(20000).execute().data)
        door = door[door["machine_code"].isin(pick)] if len(door) else door
        if len(door):
            g = door.groupby(["machine_code", "door_no", "sku_code"], as_index=False) \
                .agg(bs_pcs=("bs_qty_door", "sum"), ts_pcs=("ts_qty_door", "sum"), pemeriksaan=("bs_qty_door", "size"),
                     berbagi_sku=("shared_sku", "max"))
            st.markdown("**BS per pintu**")
            st.dataframe(g, hide_index=True)
            st.caption("BS dan TS per pintu adalah pembagian rata dari total SKU. Untuk pintu yang berbagi SKU "
                       "(berbagi_sku = True) angkanya perkiraan, bukan hitungan nyata per pintu.")
    except Exception:
        pass


def page_bs_codes(sb):
    if st.session_state.profile["role"] not in ADMIN_ROLES:
        st.warning("Role Anda tidak boleh mengubah master kode BS.")
        return
    st.header("🏷️ Master Kode BS")
    st.caption("Hanya kode yang aktif muncul di form QC. Kode yang sudah dipakai laporan jangan dihapus; "
               "nonaktifkan saja (hilangkan centang Aktif) supaya laporan lama tetap punya namanya.")
    try:
        rows = sb.table("bs_codes").select("code, name, sort_order, active").order("sort_order").execute().data
    except Exception as e:
        st.error(f"Gagal memuat (sudah menjalankan 07_qc_bs.sql?): {e}")
        return
    df = pd.DataFrame(rows, columns=["code", "name", "sort_order", "active"])
    edited = st.data_editor(
        df, key="bs_codes_editor", hide_index=True, num_rows="dynamic",
        column_config={"code": st.column_config.TextColumn("Kode", max_chars=1, required=True),
                       "name": st.column_config.TextColumn("Nama BS", required=True),
                       "sort_order": st.column_config.NumberColumn("Urutan", step=1),
                       "active": st.column_config.CheckboxColumn("Aktif", default=True)})
    active = [{"code": r["code"], "name": r["name"]} for r in rows if r.get("active")]
    if active:
        st.download_button("🖨️ Unduh lembar QC (HTML) dari kode yang aktif", bs_form.form_html(active),
                           file_name="Form_QC_BS.html", mime="text/html",
                           help="Buka di browser lalu Ctrl+P: A4, Landscape, centang Background graphics. "
                                "Cetak ulang lembar kalau daftar kode berubah.")
    if st.button("💾 Simpan kode BS", type="primary"):
        recs, errs, seen = [], [], set()
        for n, r in edited.reset_index(drop=True).iterrows():
            code = str(r["code"] or "").strip().upper()
            name = str(r["name"] or "").strip()
            if not code and not name:
                continue
            if len(code) != 1 or not code.isalpha():
                errs.append(f"Baris {n + 1}: kode harus satu huruf")
            if not name:
                errs.append(f"Baris {n + 1}: nama wajib diisi")
            if code in seen:
                errs.append(f"Baris {n + 1}: kode {code} dobel")
            seen.add(code)
            order = r["sort_order"]
            recs.append({"code": code, "name": name, "sort_order": int(order) if pd.notna(order) else n + 1,
                         "active": bool(r["active"]) if pd.notna(r["active"]) else True})
        if errs:
            st.error("Belum bisa disimpan:\n\n" + "\n".join(f"- {e}" for e in errs))
            return
        try:
            sb.table("bs_codes").upsert(recs, on_conflict="code").execute()
        except Exception as e:
            st.error(f"Gagal menyimpan: {e}")
            return
        st.session_state.pop("bs_codes", None)
        st.success(f"{len(recs)} kode tersimpan ({sum(r['active'] for r in recs)} aktif).")
