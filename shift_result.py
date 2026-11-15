"""
shift_result.py - logika murni hasil produksi akhir shift (tanpa Streamlit).

Yield = Qty hasil / Cycle count x 100. Contoh: cycle 100, qty 68 -> 68%.
Angka resmi dihitung database (kolom setting_reports.yield_pct); fungsi ini untuk pratinjau & validasi.
"""


def calc_yield(cycle_count, qty_hasil):
    if cycle_count in (None, 0) or qty_hasil is None or cycle_count < 0:
        return None
    return round(qty_hasil * 100.0 / cycle_count, 2)


def validate_result(cycle_count, qty_hasil):
    """Return (errors, warnings). Error memblokir simpan; warning hanya peringatan."""
    errors, warnings = [], []
    if cycle_count is None:
        errors.append("Cycle count wajib diisi pada laporan akhir shift")
    elif cycle_count < 1:
        errors.append("Cycle count harus lebih dari 0")
    if qty_hasil is None:
        errors.append("Qty hasil wajib diisi pada laporan akhir shift")
    elif qty_hasil < 0:
        errors.append("Qty hasil tidak boleh negatif")
    if not errors and qty_hasil > cycle_count:
        warnings.append(f"Qty hasil ({qty_hasil}) lebih besar dari cycle count ({cycle_count}), yield di atas 100%. "
                        "Pastikan angkanya benar.")
    return errors, warnings
