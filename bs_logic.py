"""
bs_logic.py - logika murni laporan BS QC (tanpa Streamlit).

BS dicatat per SKU (tipe/warna/size) per mesin per jam pemeriksaan. Kalau beberapa pintu memproduksi SKU
yang sama, BS dan TS dibagi rata ke pintu-pintu itu (dihitung database di view v_bs_door).
Jam pemeriksaan: pagi 09:00 / 13:00 / 17:00, malam 21:00 / 01:00 / 05:00. Jam 01:00 dan 05:00 masuk shift malam
yang dimulai di TANGGAL SEBELUMNYA (tanggal laporan = tanggal shift mulai).
"""
from datetime import datetime, time, timedelta

CHECKPOINTS = {"pagi": ("09:00", "13:00", "17:00"), "malam": ("21:00", "01:00", "05:00")}
AFTER_MIDNIGHT = ("01:00", "05:00")


def suggest(now):
    """(tanggal_laporan, shift, jam) pemeriksaan yang paling dekat dengan waktu `now` (datetime)."""
    best = None
    for delta in (-1, 0, 1):
        day = (now + timedelta(days=delta)).date()
        for shift, cps in CHECKPOINTS.items():
            for cp in cps:
                h, m = map(int, cp.split(":"))
                t = datetime.combine(day, time(h, m), tzinfo=now.tzinfo)
                diff = abs((t - now).total_seconds())
                if best is None or diff < best[0]:
                    rdate = day - timedelta(days=1) if cp in AFTER_MIDNIGHT else day
                    best = (diff, rdate, shift, cp)
    return best[1], best[2], best[3]


def clean_lines(rows):
    """
    rows: [{"sku_id", "label", "ts": int|None, "defects": [{"code", "qty": int|None}]}]
    Return (payload, errors). payload siap dikirim ke RPC submit_bs_report (p_lines).
    SKU tanpa kode BS = diperiksa dan tidak ada BS.
    """
    payload, errors, seen = [], [], set()
    for n, r in enumerate(rows, start=1):
        name = r.get("label") or f"SKU {n}"
        sid = r.get("sku_id")
        if sid is None:
            errors.append(f"{name}: SKU belum dipilih")
            continue
        if sid in seen:
            errors.append(f"{name}: SKU ini terisi dua kali")
            continue
        seen.add(sid)
        defects, codes = [], set()
        for d in r.get("defects", []):
            q = d.get("qty")
            if q is None or q < 1:
                errors.append(f"{name}: jumlah kode {d['code']} wajib diisi (minimal 1), atau hapus kodenya")
                continue
            if d["code"] in codes:
                errors.append(f"{name}: kode {d['code']} terisi dua kali")
                continue
            codes.add(d["code"])
            defects.append({"bs_code": d["code"], "qty": int(q)})
        payload.append({"sku_id": int(sid), "ts_qty": int(r.get("ts") or 0), "defects": defects})
    if not payload and not errors:
        errors.append("Isi minimal satu SKU yang diperiksa")
    return payload, errors


def totals(payload):
    """(total BS pcs, total TS pcs)."""
    return (sum(d["qty"] for ln in payload for d in ln["defects"]), sum(ln["ts_qty"] for ln in payload))
