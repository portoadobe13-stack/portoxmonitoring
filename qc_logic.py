"""
qc_logic.py - logika murni input QC langsung (tanpa Streamlit).

Satu input QC = satu kali simpan, berisi beberapa produk (SKU). Tiap produk: qty hasil (pasang), TS (pcs),
dan BS per kode (pcs). Angka adalah TAMBAHAN sejak input sebelumnya; total shift = jumlah semua input.
Database: 10_input_qc_hasil_dan_bs.sql
"""
from datetime import datetime, timedelta

from bs_logic import suggest


def current_shift(now):
    """(tanggal_shift_mulai, shift) untuk waktu `now`, dari jam pemeriksaan terdekat."""
    d, shift, _ = suggest(now)
    return d, shift


# ---------------------------------------------------------------- draft
def new_item():
    return {"qty": None, "ts": None, "defects": {}}


def add_defect(item, code, qty):
    """Tambah qty ke kode BS (kode yang sama dijumlahkan). qty harus >= 1."""
    if qty is None or int(qty) < 1:
        return False
    item["defects"][code] = item["defects"].get(code, 0) + int(qty)
    return True


def item_filled(item):
    return bool((item.get("qty") or 0) > 0 or (item.get("ts") or 0) > 0 or item.get("defects"))


def item_bs(item):
    return sum(item["defects"].values())


def clean_draft(draft):
    """
    draft: {sku_id: {"qty", "ts", "defects": {code: qty}}}
    Return (payload, errors). Produk yang kosong dilewati. payload siap untuk RPC submit_qc_entry (p_lines).
    """
    payload, errors = [], []
    for sid, it in draft.items():
        if not item_filled(it):
            continue
        qty = int(it.get("qty") or 0)
        ts = int(it.get("ts") or 0)
        if qty < 0 or ts < 0:
            errors.append(f"SKU {sid}: angka tidak boleh negatif")
            continue
        payload.append({"sku_id": int(sid), "qty_hasil": qty, "ts_qty": ts,
                        "defects": [{"bs_code": c, "qty": int(q)} for c, q in sorted(it["defects"].items())]})
    if not payload and not errors:
        errors.append("Belum ada yang diisi. Pilih produk lalu isi qty hasil dan/atau BS.")
    return payload, errors


def payload_totals(payload):
    """(total qty hasil, total BS, total TS)."""
    return (sum(p["qty_hasil"] for p in payload),
            sum(d["qty"] for p in payload for d in p["defects"]),
            sum(p["ts_qty"] for p in payload))


# ---------------------------------------------------------------- laporan sementara
def parse_ts(s):
    """ISO timestamp dari Supabase -> datetime aware."""
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def aggregate(entries, since=None, until=None):
    """
    entries: hasil select qc_entries dengan embed qc_entry_lines(sku_id, qty_hasil, ts_qty, qc_entry_defects(bs_code, qty)).
    since/until: datetime aware (opsional) memfilter menurut waktu input.
    Return {sku_id: {"qty", "ts", "bs", "defects": {code: qty}, "n": jumlah_input}}.
    """
    out = {}
    for e in entries:
        t = parse_ts(e["created_at"])
        if since is not None and t < since:
            continue
        if until is not None and t > until:
            continue
        for ln in e.get("qc_entry_lines") or []:
            a = out.setdefault(int(ln["sku_id"]), {"qty": 0, "ts": 0, "bs": 0, "defects": {}, "n": 0})
            a["qty"] += int(ln.get("qty_hasil") or 0)
            a["ts"] += int(ln.get("ts_qty") or 0)
            a["n"] += 1
            for d in ln.get("qc_entry_defects") or []:
                q = int(d["qty"])
                a["defects"][d["bs_code"]] = a["defects"].get(d["bs_code"], 0) + q
                a["bs"] += q
    return out


def bs_pct(bs, qty):
    """SEMENTARA: BS pcs / qty hasil x 100 (penyebut dan satuan belum diputuskan). None kalau qty kosong."""
    return round(bs * 100.0 / qty, 2) if qty else None


def since_datetime(clock, now):
    """Jam (time) terakhir sebelum/sama dengan `now`: hari ini kalau belum lewat, kalau sudah di depan maka kemarin."""
    cand = datetime.combine(now.date(), clock, tzinfo=now.tzinfo)
    return cand - timedelta(days=1) if cand > now else cand


def summary_text(machine, shift, report_date, scope_label, rows, names):
    """
    Teks ringkas untuk disalin ke kertas. rows: [(sku_id, agg)], names: {sku_id: label}.
    """
    lines = [f"{machine} · shift {shift} · {report_date.strftime('%d/%m/%Y')} · {scope_label}"]
    for sid, a in rows:
        codes = ", ".join(f"{c} {q}" for c, q in sorted(a["defects"].items())) or "-"
        lines.append(f"{names.get(sid, sid)}: hasil {a['qty']} psg · TS {a['ts']} · BS {a['bs']} ({codes})")
    tq = sum(a["qty"] for _, a in rows)
    tb = sum(a["bs"] for _, a in rows)
    tt = sum(a["ts"] for _, a in rows)
    lines.append(f"TOTAL: hasil {tq} psg · TS {tt} · BS {tb}")
    return "\n".join(lines)
