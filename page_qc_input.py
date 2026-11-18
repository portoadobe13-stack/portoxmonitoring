"""
page_qc_input.py - Input QC langsung: pilih mesin -> pilih produk -> isi qty hasil, TS, BS per kode.
QC tidak perlu tahu pintu. Waktu isi bebas; angka yang diisi adalah TAMBAHAN sejak input sebelumnya.
Tab "Laporan sementara" menjumlahkan semua input shift ini (atau sejak jam tertentu) untuk disalin ke kertas.
Database: 10_input_qc_hasil_dan_bs.sql. SKU yang sedang jalan dibaca dari laporan foreman (shift_door_skus).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import qc_logic as ql
from ui_common import running_skus, shift_doors, sku_label, sku_picker

TZ = ZoneInfo("Asia/Jakarta")
QC_ROLES = ("qc", "admin")
SHIFTS = ("pagi", "malam")


def _codes(sb):
    if "bs_codes" not in st.session_state:
        st.session_state["bs_codes"] = sb.table("bs_codes").select("code, name, sort_order") \
            .eq("active", True).order("sort_order").execute().data
    return st.session_state["bs_codes"]


def _active_machines(sb, report_date, shift):
    """{machine_id: 'jalan'|'selesai'} dari laporan foreman shift itu. Mesin tanpa laporan tidak muncul."""
    try:
        rows = sb.table("setting_reports").select("machine_id, report_type").eq("report_date", str(report_date)) \
            .eq("shift", shift).execute().data
    except Exception:
        return {}
    out = {}
    for r in rows:
        mid = r["machine_id"]
        if r["report_type"] == "akhir":
            out[mid] = "selesai"
        else:
            out.setdefault(mid, "jalan")
    return out


def _entries(sb, machine_id, report_date, shift):
    return sb.table("qc_entries") \
        .select("id, created_at, created_by, team, notes, qc_entry_lines(sku_id, qty_hasil, ts_qty, "
                "qc_entry_defects(bs_code, qty))") \
        .eq("machine_id", machine_id).eq("report_date", str(report_date)).eq("shift", shift) \
        .order("created_at").execute().data


def _btn_grid(items, per_row, key_prefix, selected):
    """Tombol pilihan. items: [(value, label)]. Return value yang diklik, atau None."""
    clicked = None
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for col, (val, label) in zip(cols, items[i:i + per_row]):
            if col.button(label, key=f"{key_prefix}_{val}", use_container_width=True,
                          type="primary" if val == selected else "secondary"):
                clicked = val
    return clicked


def page_qc_input(sb, master):
    role = st.session_state.profile["role"]
    if role not in QC_ROLES:
        st.warning("Role Anda tidak boleh mengisi input QC.")
        return
    try:
        codes = _codes(sb)
    except Exception as e:
        st.error(f"Gagal memuat kode BS: {e}")
        return
    if not codes:
        st.error("Belum ada kode BS aktif. Admin perlu mengaktifkan kode di menu Master Kode BS.")
        return
    skus = master["skus"]
    ss = st.session_state
    gen = ss.setdefault("qci_gen", 0)

    st.header("✍️ Input QC: Hasil dan BS")

    if ss.get("qci_flash"):
        st.success(ss.pop("qci_flash"))

    # ---------- shift
    now = datetime.now(TZ)
    d0, s0 = ql.current_shift(now)
    with st.expander(f"Shift: **{s0}**, tanggal {d0.strftime('%d/%m/%Y')} (ubah kalau input untuk shift lain)"):
        c1, c2 = st.columns(2)
        report_date = c1.date_input("Tanggal produksi (tanggal shift mulai)", d0, format="DD/MM/YYYY", key="qci_date")
        shift = c2.selectbox("Shift", SHIFTS, index=SHIFTS.index(s0), key="qci_shift")

    # ---------- mesin
    status = _active_machines(sb, report_date, shift)
    st.subheader("1. Pilih mesin")
    id2code = {v: k for k, v in master["machines"].items()}
    items = []
    for code, mid in master["machines"].items():
        st_ = status.get(mid)
        mark = "🟢 " if st_ == "jalan" else ("⚪ " if st_ == "selesai" else "")
        items.append((mid, f"{mark}{code}"))
    sel_m = ss.get("qci_machine")
    hit = _btn_grid(items, 3, "qci_m", sel_m)
    if hit is not None and hit != sel_m:
        ss["qci_machine"] = sel_m = hit
        ss.pop("qci_sku", None)
        st.rerun()
    st.caption("🟢 sedang jalan (sudah ada laporan awal, belum ada laporan akhir) · ⚪ shift sudah ditutup foreman · "
               "tanpa tanda = belum ada laporan foreman")
    if sel_m is None:
        st.info("Tap mesin untuk mulai.")
        return
    machine = id2code[sel_m]

    # ---------- produk
    pk = f"qci_doors_{sel_m}_{report_date}_{shift}"
    if pk not in ss:
        ss[pk] = shift_doors(sb, sel_m, report_date, shift)
    running = running_skus(ss[pk])
    extra_key = f"qci_extra_{sel_m}_{report_date}_{shift}"
    extra = ss.setdefault(extra_key, [])
    prod_ids = list(running) + [x for x in extra if x not in running]

    draft_key = f"qci_draft_{gen}_{sel_m}_{report_date}_{shift}"
    draft = ss.setdefault(draft_key, {})

    st.subheader("2. Pilih produk")
    if not running:
        st.warning("Laporan foreman untuk mesin dan shift ini belum ada, jadi daftar produk kosong. "
                   "Tambahkan produk lewat 'Produk lain' di bawah.")
    items = []
    for sid in prod_ids:
        filled = sid in draft and ql.item_filled(draft[sid])
        items.append((sid, ("✅ " if filled else "") + sku_label(skus, sid)))
    sel_p = ss.get("qci_sku")
    if sel_p not in prod_ids:
        sel_p = None
    if items:
        hit = _btn_grid(items, 2, f"qci_p_{sel_m}", sel_p)
        if hit is not None and hit != sel_p:
            ss["qci_sku"] = sel_p = hit
            st.rerun()
    with st.expander("➕ Produk lain (di luar daftar)"):
        new_sid = sku_picker(skus, f"qci_x_{sel_m}")
        if st.button("Tambahkan ke daftar", key=f"qci_xadd_{sel_m}", disabled=new_sid is None):
            if new_sid not in prod_ids:
                extra.append(new_sid)
            ss["qci_sku"] = new_sid
            st.rerun()

    tab_in, tab_rep, tab_hist = st.tabs(["✍️ Isi", "📋 Laporan sementara", "🕘 Riwayat input"])

    # =================================================== ISI
    with tab_in:
        if sel_p is None:
            st.info("Tap salah satu produk di atas untuk mengisi hasil dan BS-nya.")
        else:
            item = draft.setdefault(sel_p, ql.new_item())
            with st.container(border=True):
                st.markdown(f"**{sku_label(skus, sel_p)}**")
                if sel_p in running:
                    st.caption("Pintu " + ", ".join(str(x) for x in running[sel_p]) + " (tidak perlu diisi per pintu)")
                c1, c2 = st.columns(2)
                q = c1.number_input("Qty hasil (pasang)", min_value=0, step=1, value=item["qty"],
                                    placeholder="0", key=f"qci_q_{gen}_{sel_p}",
                                    help="Tambahan hasil sejak input sebelumnya, bukan total.")
                t = c2.number_input("TS (pcs, tidak sepasang)", min_value=0, step=1, value=item["ts"],
                                    placeholder="0", key=f"qci_t_{gen}_{sel_p}")
                item["qty"] = int(q) if q is not None else None
                item["ts"] = int(t) if t is not None else None

                st.markdown("**BS**")
                an = ss.setdefault(f"qci_an_{gen}_{sel_p}", 0)
                labels = {f"{c['code']} · {c['name']}": c["code"] for c in codes}
                b1, b2, b3 = st.columns([3, 2, 2])
                pick = b1.selectbox("Kode BS", list(labels), index=None, placeholder="Pilih kode BS",
                                    key=f"qci_bc_{gen}_{sel_p}_{an}")
                bq = b2.number_input("Jumlah (pcs)", min_value=1, step=1, value=1, key=f"qci_bq_{gen}_{sel_p}_{an}")
                b3.write("")
                if b3.button("➕ Tambah", key=f"qci_badd_{gen}_{sel_p}_{an}", use_container_width=True,
                             disabled=pick is None):
                    ql.add_defect(item, labels[pick], int(bq))
                    ss[f"qci_an_{gen}_{sel_p}"] = an + 1
                    st.rerun()
                names = {c["code"]: c["name"] for c in codes}
                for code, qty in sorted(item["defects"].items()):
                    r1, r2 = st.columns([5, 1])
                    r1.markdown(f"**{code}** · {names.get(code, '')}: **{qty}** pcs")
                    if r2.button("✕", key=f"qci_bdel_{gen}_{sel_p}_{code}", help="Hapus kode ini"):
                        item["defects"].pop(code)
                        st.rerun()
                if not item["defects"]:
                    st.caption("Belum ada BS untuk produk ini. Pilih kode, isi jumlah, lalu Tambah. "
                               "Kode yang sama ditambah lagi akan dijumlahkan.")
                if ql.item_filled(item) and st.button("🗑 Kosongkan produk ini", key=f"qci_clear_{gen}_{sel_p}"):
                    draft[sel_p] = ql.new_item()
                    for k in (f"qci_q_{gen}_{sel_p}", f"qci_t_{gen}_{sel_p}"):
                        ss.pop(k, None)
                    st.rerun()

        # ---- ringkasan + simpan
        payload, errors = ql.clean_draft(draft)
        st.markdown("---")
        st.subheader("3. Simpan")
        if payload:
            tq, tb, tt = ql.payload_totals(payload)
            rows = []
            for p in payload:
                rows.append({"Produk": sku_label(skus, p["sku_id"]), "Hasil (psg)": p["qty_hasil"],
                             "TS": p["ts_qty"], "BS": sum(d["qty"] for d in p["defects"]),
                             "Kode BS": ", ".join(f"{d['bs_code']} {d['qty']}" for d in p["defects"]) or "-"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.info(f"{machine} · hasil **{tq}** pasang · BS **{tb}** pcs · TS **{tt}** pcs. "
                    "Angka ini ditambahkan ke yang sudah tersimpan.")
        else:
            st.caption("Belum ada yang diisi.")
        s1, s2 = st.columns([1, 3])
        team = s1.selectbox("Tim", ["A", "B", "C"], key=f"qci_team_{gen}")
        notes = s2.text_input("Catatan (opsional)", key=f"qci_notes_{gen}")
        if st.button("💾 Simpan input QC", type="primary", key=f"qci_save_{gen}", disabled=not payload):
            if errors:
                st.error("Belum bisa disimpan:\n\n" + "\n".join(f"- {e}" for e in errors))
            else:
                meta = {"machine_id": sel_m, "report_date": str(report_date), "shift": shift, "team": team,
                        "notes": notes or None}
                try:
                    eid = sb.rpc("submit_qc_entry", {"p_entry": meta, "p_lines": payload}).execute().data
                except Exception as e:
                    msg = str(e)
                    if "submit_qc_entry" in msg and ("does not exist" in msg or "Could not find" in msg):
                        st.error("Database belum diperbarui untuk input QC. Jalankan 10_input_qc_hasil_dan_bs.sql "
                                 "di Supabase → SQL Editor, lalu coba simpan lagi.")
                    else:
                        st.error(f"Gagal menyimpan: {msg}")
                else:
                    tq, tb, tt = ql.payload_totals(payload)
                    ss["qci_flash"] = (f"Input #{eid} tersimpan ({now.strftime('%H:%M')}): {machine}, "
                                       f"hasil {tq} pasang, BS {tb} pcs, TS {tt} pcs. Cek totalnya di tab Laporan sementara.")
                    ss["qci_gen"] = gen + 1
                    ss.pop("qci_sku", None)
                    st.rerun()

    # =================================================== LAPORAN SEMENTARA
    with tab_rep:
        try:
            entries = _entries(sb, sel_m, report_date, shift)
        except Exception as e:
            msg = str(e)
            st.error("Database belum diperbarui untuk input QC. Jalankan 10_input_qc_hasil_dan_bs.sql di Supabase."
                     if "qc_entries" in msg else f"Gagal memuat: {msg}")
            entries = []
        scope = st.radio("Cakupan", ["Seluruh shift", "Sejak jam…"], horizontal=True, key="qci_scope")
        since = None
        if scope != "Seluruh shift":
            clock = st.time_input("Hitung input sejak jam", value=now.replace(second=0, microsecond=0).time(),
                                  key="qci_since", step=900)
            since = ql.since_datetime(clock, now)
            st.caption("Cocok kalau baru saja menulis di kertas: isi jam laporan kertas terakhir, "
                       "maka hanya input setelah itu yang dijumlahkan.")
        agg = ql.aggregate(entries, since=since)
        if not agg:
            st.info(f"Belum ada input QC untuk {machine} / shift {shift}" + (" pada rentang ini." if since else "."))
        else:
            fm = {}
            if since is None:
                try:
                    fr = sb.table("v_qc_shift_sku").select("sku_id, qty_foreman").eq("machine_id", sel_m) \
                        .eq("report_date", str(report_date)).eq("shift", shift).execute().data
                    fm = {int(r["sku_id"]): r["qty_foreman"] for r in fr if r.get("qty_foreman") is not None}
                except Exception:
                    fm = {}
            all_codes = sorted({c for a in agg.values() for c in a["defects"]})
            rows = []
            for sid, a in sorted(agg.items()):
                r = {"Produk": sku_label(skus, sid), "Input": a["n"], "Hasil (psg)": a["qty"], "TS": a["ts"],
                     "BS": a["bs"], "BS% (sementara)": ql.bs_pct(a["bs"], a["qty"])}
                for c in all_codes:
                    r[c] = a["defects"].get(c, 0)
                if since is None and fm:
                    r["Hasil foreman (akhir)"] = fm.get(sid)
                rows.append(r)
            tot = {"Produk": "TOTAL", "Input": len(entries) if since is None else sum(a["n"] for a in agg.values()),
                   "Hasil (psg)": sum(a["qty"] for a in agg.values()), "TS": sum(a["ts"] for a in agg.values()),
                   "BS": sum(a["bs"] for a in agg.values())}
            tot["BS% (sementara)"] = ql.bs_pct(tot["BS"], tot["Hasil (psg)"])
            for c in all_codes:
                tot[c] = sum(a["defects"].get(c, 0) for a in agg.values())
            if since is None and fm:
                tot["Hasil foreman (akhir)"] = sum(fm.get(s, 0) or 0 for s in agg)
            rows.append(tot)
            m1, m2, m3 = st.columns(3)
            m1.metric("Hasil (pasang)", tot["Hasil (psg)"])
            m2.metric("BS (pcs)", tot["BS"])
            m3.metric("TS (pcs)", tot["TS"])
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            st.caption("Input = berapa kali produk ini diisi. BS% sementara = BS pcs ÷ hasil pasang × 100; "
                       "satuan dan penyebut menunggu keputusan tim pabrik. "
                       + ("Hasil foreman muncul setelah foreman menutup shift; bandingkan dengan hasil QC."
                          if since is None else ""))
            if since is None and fm:
                diff = [(sku_label(skus, s), agg[s]["qty"] - (fm[s] or 0)) for s in agg if s in fm]
                if diff:
                    st.markdown("**Selisih hasil QC − foreman**")
                    st.dataframe(pd.DataFrame(diff, columns=["Produk", "Selisih (psg)"]), hide_index=True)
            scope_label = "seluruh shift" if since is None else f"sejak {since.strftime('%H:%M')}"
            names = {sid: sku_label(skus, sid) for sid in agg}
            with st.expander("📝 Teks ringkas untuk disalin ke kertas"):
                st.code(ql.summary_text(machine, shift, report_date, scope_label, sorted(agg.items()), names),
                        language=None)
        try:
            paper = sb.table("bs_reports").select("checkpoint").eq("machine_id", sel_m) \
                .eq("report_date", str(report_date)).eq("shift", shift).execute().data
        except Exception:
            paper = []
        if paper:
            st.caption(f"Catatan: shift ini juga punya {len(paper)} laporan dari lembar kertas/foto "
                       "(menu BS QC - Foto Lembar). Itu tidak dihitung di sini; dashboard admin menjumlahkan keduanya, "
                       "jadi jangan memasukkan data yang sama dua kali.")

    # =================================================== RIWAYAT
    with tab_hist:
        try:
            entries = _entries(sb, sel_m, report_date, shift)
        except Exception:
            entries = []
        if not entries:
            st.info("Belum ada input.")
        me = st.session_state.profile.get("id")
        for e in reversed(entries):
            t = ql.parse_ts(e["created_at"]).astimezone(TZ)
            lines = []
            for ln in e.get("qc_entry_lines") or []:
                codes_txt = ", ".join(f"{d['bs_code']} {d['qty']}" for d in ln.get("qc_entry_defects") or []) or "-"
                lines.append(f"- {sku_label(skus, int(ln['sku_id']))}: hasil {ln['qty_hasil']}, TS {ln['ts_qty']}, BS {codes_txt}")
            with st.container(border=True):
                st.markdown(f"**#{e['id']} · {t.strftime('%H:%M')}** · tim {e.get('team') or '-'}"
                            + (f" · {e['notes']}" if e.get("notes") else ""))
                st.markdown("\n".join(lines))
                mine = role == "admin" or (me is not None and e.get("created_by") == me)
                if mine:
                    ck = f"qci_del_{e['id']}"
                    if not ss.get(ck):
                        if st.button("🗑 Hapus input ini", key=f"{ck}_b"):
                            ss[ck] = True
                            st.rerun()
                    else:
                        st.warning("Hapus input ini? Angkanya hilang dari total.")
                        d1, d2 = st.columns(2)
                        if d1.button("Ya, hapus", key=f"{ck}_y", type="primary"):
                            try:
                                sb.table("qc_entries").delete().eq("id", e["id"]).execute()
                                ss["qci_flash"] = f"Input #{e['id']} dihapus."
                            except Exception as ex:
                                ss["qci_flash"] = f"Gagal menghapus: {ex}"
                            ss.pop(ck, None)
                            st.rerun()
                        if d2.button("Batal", key=f"{ck}_n"):
                            ss.pop(ck, None)
                            st.rerun()
