"""
shift_result.py - logika murni hasil produksi akhir shift PER PINTU (tanpa Streamlit).

Aturan:
  - cycle count dicatat per pintu (dari HMI mesin)
  - qty hasil dicatat per SKU (size) = total semua pintu yang memproduksi SKU itu
  - qty per pintu = qty SKU dibagi rata ke pintu yang memproduksi SKU yang sama
  - yield per pintu = qty per pintu / cycle count pintu x 100
Angka resmi dihitung database (view v_door_yield dan v_shift_yield); modul ini untuk pratinjau & validasi.

rows        : list dict {"door_no", "sku_id", "cycle_count"} (pintu idle tidak ikut)
qty_by_sku  : dict {sku_id: qty_hasil}
"""


def group_doors(rows):
    groups = {}
    for r in rows:
        groups.setdefault(r["sku_id"], []).append(r)
    return groups


def preview(rows, qty_by_sku):
    """Satu dict per pintu: door_no, sku_id, cycle_count, doors_same_sku, qty_door, yield_door, yield_sku."""
    out = []
    for sku, ds in group_doors(rows).items():
        q, n = qty_by_sku.get(sku), len(ds)
        cycles = sum(d["cycle_count"] for d in ds)
        for d in ds:
            ok = q is not None and d["cycle_count"]
            out.append({
                "door_no": d["door_no"], "sku_id": sku, "cycle_count": d["cycle_count"], "doors_same_sku": n,
                "qty_door": round(q / n, 2) if q is not None else None,
                "yield_door": round(q / n * 100.0 / d["cycle_count"], 2) if ok else None,
                "yield_sku": round(q * 100.0 / cycles, 2) if q is not None and cycles else None,
            })
    return sorted(out, key=lambda x: x["door_no"])


def shift_yield(rows, qty_by_sku):
    """(total_cycle, total_qty, yield_pct) seluruh mesin. None kalau belum lengkap."""
    cycles = sum(r["cycle_count"] for r in rows)
    skus = {r["sku_id"] for r in rows}
    if not rows or cycles == 0 or any(qty_by_sku.get(s) is None for s in skus):
        return None
    qty = sum(qty_by_sku[s] for s in skus)
    return cycles, qty, round(qty * 100.0 / cycles, 2)


def validate_results(rows, qty_by_sku):
    """Return (errors, warnings). Error memblokir simpan; warning hanya peringatan.
    rows hanya berisi pintu yang sudah lengkap (SKU dan cycle terisi); kelengkapan per pintu dicek form."""
    errors, warnings = [], []
    if not rows:
        errors.append("Isi cycle count minimal untuk satu pintu yang jalan")
        return errors, warnings
    for sku, ds in group_doors(rows).items():
        q = qty_by_sku.get(sku)
        doors = ", ".join(str(d["door_no"]) for d in ds)
        if q is None:
            errors.append(f"Qty hasil wajib diisi untuk SKU di pintu {doors}")
        elif q < 0:
            errors.append(f"Qty hasil SKU di pintu {doors} tidak boleh negatif")
        elif q > sum(d["cycle_count"] for d in ds):
            warnings.append(f"Qty hasil SKU di pintu {doors} ({q}) lebih besar dari total cycle count "
                            f"({sum(d['cycle_count'] for d in ds)}), yield di atas 100%. Pastikan angkanya benar.")
    return errors, warnings
