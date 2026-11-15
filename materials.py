"""
materials.py - logika murni input bahan per sak (tanpa Streamlit).

Satu laporan boleh punya beberapa baris bahan, tiap baris: jenis (L/S), jumlah (sak), tanggal bahan.
Contoh: Bahan L = 3 sak tanggal 11/11/2026, Bahan S = 2 sak tanggal 09/11/2026.
"""
from datetime import date

MATERIAL_TYPES = ("L", "S")


def _fmt_qty(q):
    return f"{q:g}"


def _fmt_date(d):
    return d.strftime("%d/%m/%Y")


def clean_rows(rows, report_date, required=False):
    """
    rows: list dict {"type": "L"|"S", "qty": float|None, "date": date|None} dari form.
    Baris yang jumlah DAN tanggalnya kosong dianggap kosong dan diabaikan.
    Return (payload, errors). payload siap dikirim ke RPC (p_materials).
    """
    payload, errors, seen = [], [], set()
    for n, r in enumerate(rows, start=1):
        t, q, d = r.get("type"), r.get("qty"), r.get("date")
        if t not in MATERIAL_TYPES:
            errors.append(f"Bahan baris {n}: jenis harus L atau S")
            continue
        if q in (None, 0) and d is None:
            continue                                    # baris kosong
        name = f"Bahan {t} (baris {n})"
        if q is None or q <= 0:
            errors.append(f"{name}: jumlah sak wajib diisi (lebih dari 0)")
            continue
        if d is None:
            errors.append(f"{name}: tanggal bahan wajib diisi")
            continue
        if d > report_date:
            errors.append(f"{name}: tanggal bahan ({_fmt_date(d)}) tidak boleh setelah tanggal produksi "
                          f"({_fmt_date(report_date)})")
            continue
        if (t, d) in seen:
            errors.append(f"{name}: bahan {t} dengan tanggal {_fmt_date(d)} sudah ada, gabungkan jumlahnya")
            continue
        seen.add((t, d))
        payload.append({"material_type": t, "qty_sak": round(float(q), 2), "material_date": d.isoformat()})
    if required and not payload and not errors:
        errors.append("Isi minimal satu bahan (jenis, jumlah sak, tanggal bahan)")
    return payload, errors


def summary(payload):
    """Ringkasan teks untuk kolom material_mix, mis. 'L 3 sak (11/11/2026) + S 2 sak (09/11/2026)'."""
    items = sorted(payload, key=lambda p: (MATERIAL_TYPES.index(p["material_type"]), p["material_date"]))
    return " + ".join(
        f"{p['material_type']} {_fmt_qty(p['qty_sak'])} sak ({_fmt_date(date.fromisoformat(p['material_date']))})"
        for p in items) or None
