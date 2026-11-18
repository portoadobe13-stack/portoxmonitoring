"""
shift_result.py - logika murni hasil produksi akhir shift PER PINTU (tanpa Streamlit).

Aturan:
  - cycle count dicatat per pintu (dari HMI mesin)
  - satu pintu bisa menjalankan 1 sampai 4 pasang per siklus (2 cetakan x 2 size); satu pasang = satu "slot"
  - qty hasil dicatat per SKU (size), satuan PASANG, = total semua slot yang memproduksi SKU itu
  - qty per slot = qty SKU dibagi rata ke semua slot SKU yang sama (di pintu manapun); qty pintu = jumlah qty slotnya
  - yield pintu = qty pintu / (cycle count x jumlah slot pintu itu) x 100
Angka resmi dihitung database (view v_door_yield dan v_shift_yield); modul ini untuk pratinjau & validasi.

rows        : list dict {"door_no", "cycle_count", "sku_ids": [sku_id per slot]} (pintu idle tidak ikut)
qty_by_sku  : dict {sku_id: qty_hasil}
"""


def _slots(rows):
    return [(r["door_no"], r["cycle_count"], sid) for r in rows for sid in r["sku_ids"]]


def group_doors(rows):
    """{sku_id: [ {"door_no", "cycle_count"} per slot ]}. Pintu yang sama muncul dua kali kalau SKU itu ada di dua slot."""
    groups = {}
    for door, cyc, sid in _slots(rows):
        groups.setdefault(sid, []).append({"door_no": door, "cycle_count": cyc})
    return groups


def preview(rows, qty_by_sku):
    """Satu dict per pintu: door_no, sku_ids, cycle_count, pairs, qty_door, yield_door."""
    groups = group_doors(rows)
    out = []
    for r in rows:
        pairs = len(r["sku_ids"])
        shares = []
        for sid in r["sku_ids"]:
            q = qty_by_sku.get(sid)
            shares.append(None if q is None else q / len(groups[sid]))
        qd = None if any(x is None for x in shares) else sum(shares)
        out.append({"door_no": r["door_no"], "sku_ids": list(r["sku_ids"]), "cycle_count": r["cycle_count"],
                    "pairs": pairs,
                    "qty_door": round(qd, 2) if qd is not None else None,
                    "yield_door": round(qd * 100.0 / (r["cycle_count"] * pairs), 2)
                                  if qd is not None and r["cycle_count"] else None})
    return sorted(out, key=lambda x: x["door_no"])


def preview_sku(rows, qty_by_sku):
    """Satu dict per SKU: sku_id, doors, slots, qty, cycles (total cycle semua slot SKU itu), yield_sku."""
    out = []
    for sid, sl in group_doors(rows).items():
        q = qty_by_sku.get(sid)
        cycles = sum(s["cycle_count"] for s in sl)
        out.append({"sku_id": sid, "doors": sorted({s["door_no"] for s in sl}), "slots": len(sl), "qty": q,
                    "cycles": cycles,
                    "yield_sku": round(q * 100.0 / cycles, 2) if q is not None and cycles else None})
    return sorted(out, key=lambda x: x["doors"])


def shift_yield(rows, qty_by_sku):
    """(total_cycle, total_kapasitas, total_qty, yield_pct) seluruh mesin. None kalau belum lengkap.
    Kapasitas = jumlah (cycle x pasang per siklus) semua pintu."""
    skus = {sid for r in rows for sid in r["sku_ids"]}
    cycles = sum(r["cycle_count"] for r in rows)
    cap = sum(r["cycle_count"] * len(r["sku_ids"]) for r in rows)
    if not rows or cap == 0 or any(qty_by_sku.get(s) is None for s in skus):
        return None
    qty = sum(qty_by_sku[s] for s in skus)
    return cycles, cap, qty, round(qty * 100.0 / cap, 2)


def validate_results(rows, qty_by_sku):
    """Return (errors, warnings). Error memblokir simpan; warning hanya peringatan.
    rows hanya berisi pintu yang sudah lengkap (SKU dan cycle terisi); kelengkapan per pintu dicek form."""
    errors, warnings = [], []
    if not rows:
        errors.append("Isi cycle count minimal untuk satu pintu yang jalan")
        return errors, warnings
    for r in rows:
        if not 1 <= len(r["sku_ids"]) <= 4:
            errors.append(f"Pintu {r['door_no']}: jumlah pasang per siklus harus 1 sampai 4")
    for sku, sl in group_doors(rows).items():
        q = qty_by_sku.get(sku)
        doors = ", ".join(str(d) for d in sorted({s["door_no"] for s in sl}))
        total = sum(s["cycle_count"] for s in sl)
        if q is None:
            errors.append(f"Qty hasil wajib diisi untuk SKU di pintu {doors}")
        elif q < 0:
            errors.append(f"Qty hasil SKU di pintu {doors} tidak boleh negatif")
        elif q > total:
            warnings.append(f"Qty hasil SKU di pintu {doors} ({q}) lebih besar dari total cycle count "
                            f"({total}), yield di atas 100%. Pastikan angkanya benar.")
    return errors, warnings
