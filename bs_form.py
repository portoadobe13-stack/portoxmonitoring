"""
bs_form.py - lembar QC BS cetak (HTML, A4 landscape) dari daftar kode BS aktif.
Satu lembar = satu mesin = satu shift. Tiga blok pemeriksaan; baris S1-S6 mengacu ke daftar SKU di atas.
Kalau daftar kode BS berubah, cetak ulang lembarnya (jumlah dan urutan kolom ikut berubah, OCR membaca urutan itu).
Dibuka di browser lalu Ctrl+P -> A4, Landscape, margin Default/None, centang "Background graphics".
"""
from html import escape

WINDOWS = (("1", "09:00", "21:00"), ("2", "13:00", "01:00"), ("3", "17:00", "05:00"))
SKU_ROWS = 6


def form_html(codes, version="v1"):
    """codes: [{"code": "A", "name": "Bisul"}, ...] urut seperti di form QC."""
    head = "".join(f"<th class='k'><b>{escape(c['code'])}</b><i>{escape(c['name'])}</i></th>" for c in codes)
    cells = "<td class='c'></td>" * (1 + len(codes))

    def block(no, pagi, malam):
        rows = "".join(f"<tr><th class='s'>S{i}</th>{cells}</tr>" for i in range(1, SKU_ROWS + 1))
        return (f"<div class='blk'><div class='bh'><b>Pemeriksaan {no}</b>"
                f"<span>Pagi {pagi} &nbsp;|&nbsp; Malam {malam}</span>"
                f"<span>&#9744; Diperiksa (centang; sel kosong = 0)</span></div>"
                f"<table class='g'><tr><th class='s'>SKU</th><th class='k'><b>TS</b><i>tdk sepasang</i></th>{head}</tr>"
                f"{rows}</table></div>")

    sku_rows = "".join(f"<tr><th class='s'>S{i}</th><td class='w'></td><td class='w'></td><td class='w'></td></tr>"
                       for i in range(1, SKU_ROWS + 1))
    blocks = "".join(block(*w) for w in WINDOWS)
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Form QC BS</title><style>
@page {{ size: A4 landscape; margin: 7mm; }}
*{{box-sizing:border-box}} body{{font-family:Arial,Helvetica,sans-serif;font-size:9pt;margin:0;width:283mm;padding:0 6mm}}
.top{{display:flex;gap:6mm;height:38mm}} .f{{width:112mm}} .f h1{{font-size:13pt;margin:0 0 2mm}}
.f div{{margin:1.6mm 0}} .ln{{display:inline-block;border-bottom:1px solid #000;width:38mm}}
.sku{{flex:1}} .sku table{{width:100%;border-collapse:collapse}}
.sku th,.sku td{{border:1px solid #000;height:5mm;padding:0 1mm;font-size:8pt}} .t{{font-weight:bold;margin-bottom:1mm;font-size:8.5pt}}
.blk{{margin-top:2.5mm}} .bh{{display:flex;justify-content:space-between;background:#222;color:#fff;padding:1mm 2mm;font-size:8.5pt;
 -webkit-print-color-adjust:exact;print-color-adjust:exact}}
.g{{width:100%;border-collapse:collapse;table-layout:fixed}} .g th,.g td{{border:1px solid #000}} .g td.c{{height:5.6mm}}
th.s{{width:12mm;background:#eee;font-size:8pt}} th.k{{background:#eee;height:8mm;line-height:1.05}}
th.k b{{font-size:10pt;display:block}} th.k i{{font-size:5.6pt;font-style:normal;display:block}}
.foot{{margin-top:2mm;font-size:7pt;display:flex;justify-content:space-between;color:#333}}
.mark{{position:fixed;width:4mm;height:4mm;background:#000}}
</style></head><body>
<div class='top'>
 <div class='f'><h1>PORTO X &middot; LAPORAN BS QC</h1>
  <div>Mesin: <span class='ln' style='width:28mm'></span> &nbsp; Tanggal produksi: <span class='ln' style='width:30mm'></span></div>
  <div>Shift: &#9744; Pagi &nbsp; &#9744; Malam &nbsp;&nbsp;&nbsp; Tim: &#9744; A &nbsp; &#9744; B &nbsp; &#9744; C</div>
  <div>Nama QC: <span class='ln' style='width:60mm'></span></div>
  <div style='font-size:7.5pt;color:#333'>Tanggal produksi = tanggal shift mulai (jam 01:00 dan 05:00 memakai tanggal shift malam yang dimulai kemarin).
  Tulis angka, bukan garis tally. Warna ditulis kode angka.</div></div>
 <div class='sku'><div class='t'>DAFTAR SKU SHIFT INI (tulis sekali, dipakai di 3 pemeriksaan)</div>
  <table><tr><th class='s'>No</th><th>Tipe (mis. 6004)</th><th>Warna (kode angka)</th><th>Size (mis. 39/40)</th></tr>{sku_rows}</table></div>
</div>
{blocks}
<div class='foot'><span>FORM QC-BS {escape(version)} &middot; {len(codes)} kode BS</span>
<span>Foto seluruh lembar, tegak, tanpa bayangan, empat sudut terlihat</span></div>
<div class='mark' style='left:0;top:0'></div><div class='mark' style='right:0;top:0'></div>
<div class='mark' style='left:0;bottom:0'></div><div class='mark' style='right:0;bottom:0'></div>
</body></html>"""
