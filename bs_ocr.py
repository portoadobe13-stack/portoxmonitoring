"""
bs_ocr.py - OCR lembar tulisan tangan BS QC (tanpa Streamlit). Template lembar: bs_form.py.

Alur: foto -> scan_form() -> parsed -> match_row() (cocokkan tulisan SKU ke SKU yang sedang jalan)
      -> QC memverifikasi di layar -> build_reports() -> submit_bs_report per pemeriksaan.
Aturan keselamatan: sel yang ada tulisannya tapi tidak terbaca = None dan HARUS diisi QC sebelum simpan;
sel kosong di blok yang diperiksa = 0.
"""
import json
import re
from datetime import date, datetime

import ocr_core as oc

MAX_SIDE = 2400          # lembar padat, perlu resolusi lebih tinggi daripada layar HMI (1600)
SKU_ROWS = 6
WINDOWS = 3
CHECKPOINTS = {"pagi": ("09:00", "13:00", "17:00"), "malam": ("21:00", "01:00", "05:00")}


# ----------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------
def build_prompt(codes):
    """codes: [{"code","name"}] urut kolom di lembar (setelah kolom TS)."""
    n = 1 + len(codes)
    legend = ", ".join(f"{c['code']}={c['name']}" for c in codes)
    example = {
        "status": "success",
        "header": {"mesin": None, "tanggal": None, "shift": None, "tim": None},
        "skus": [[None, None, None] for _ in range(SKU_ROWS)],
        "blocks": [{"checked": False, "rows": [[None] * n for _ in range(SKU_ROWS)]} for _ in range(WINDOWS)],
    }
    return "\n".join([
        'Foto ini adalah lembar tulisan tangan "LAPORAN BS QC" Porto X untuk SATU mesin dan SATU shift (kertas A4 landscape).',
        "Isinya: (1) kepala form: Mesin, Tanggal produksi, Shift (Pagi/Malam), Tim (A/B/C); "
        f"(2) tabel DAFTAR SKU baris S1-S{SKU_ROWS} dengan kolom Tipe, Warna, Size; "
        f"(3) {WINDOWS} blok \"Pemeriksaan 1/2/3\". Tiap blok punya kotak \"Diperiksa\" dan tabel baris S1-S{SKU_ROWS} "
        f"dengan {n} kolom berurutan: TS, " + ", ".join(c["code"] for c in codes) + ".",
        f"Arti kolom: TS=tidak sepasang, {legend}.",
        "",
        "Aturan membaca:",
        "- Baca persis tulisan tangan. Tipe berupa angka (mis. 6004). Warna berupa kode angka (mis. 88, 00, 62). "
        "Size mis. 39/40. Tulis sebagai teks apa adanya; baris SKU yang kosong semua diisi null.",
        "- Sel angka yang benar-benar kosong (tidak ada tulisan) = 0. Sel yang ADA tulisan tetapi tidak terbaca jelas = null. "
        "Jangan menebak.",
        f"- Selalu {n} nilai per baris dan {SKU_ROWS} baris per blok; jangan menggeser kolom atau baris.",
        '- "checked": true jika kotak "Diperiksa" pada blok itu dicentang atau ditandai; false jika kosong.',
        '- header.shift: "pagi" atau "malam" menurut kotak yang dicentang (null jika tidak jelas). '
        'header.tim: "A", "B", atau "C". header.mesin dan header.tanggal ditulis apa adanya (mis. M-06, 15/11/2026).',
        "",
        "Jawab HANYA dengan satu objek JSON dalam SATU baris tanpa spasi, indentasi, atau komentar. Bentuk "
        "(ganti null dengan nilai yang dibaca):",
        json.dumps(example, ensure_ascii=False, separators=(",", ":")),
        "",
        'Jika foto BUKAN lembar tersebut, terlalu buram, atau terpotong sehingga tabel tidak bisa dibaca, jawab: '
        '{"status":"error","message":"alasan singkat"}',
    ])


# ----------------------------------------------------------------------
# Memanggil model
# ----------------------------------------------------------------------
def scan_form(provider, client, model, img, codes):
    """Return (parsed, error, usage). Tidak melempar exception."""
    prompt = build_prompt(codes)
    usage, js = None, None
    if provider == "claude":
        messages = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": oc.image_to_b64(img, 88)}},
            {"type": "text", "text": prompt}]}]
        try:
            resp = client.messages.create(model=model, max_tokens=4000, messages=messages)
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            js = oc.extract_json(text)
            usage = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
        except Exception as e:
            return None, oc._claude_error(e, model), None
    else:
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": oc.image_to_data_url(img, 88)}}]}]
        last = None
        for extra in (dict(response_format={"type": "json_object"}), dict()):
            try:
                comp = client.chat.completions.create(model=model, messages=messages, temperature=0,
                                                      max_completion_tokens=4000, **extra)
                js = oc.extract_json(comp.choices[0].message.content)
                u = getattr(comp, "usage", None)
                usage = {"in": getattr(u, "prompt_tokens", None), "out": getattr(u, "completion_tokens", None)} if u else None
                break
            except Exception as e:
                last = e
        if js is None:
            return None, f"Gagal memanggil/menafsirkan jawaban Groq: {last}", None
    if js.get("status") != "success":
        return None, js.get("message", "Foto tidak valid"), usage
    try:
        return parse_response(js, len(codes)), None, usage
    except Exception as e:
        return None, f"Jawaban model tidak sesuai bentuk lembar: {e}", usage


# ----------------------------------------------------------------------
# Membaca jawaban model
# ----------------------------------------------------------------------
def _int(v):
    """Angka bulat >= 0 atau None (tidak terbaca)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() and v >= 0 else None
    s = str(v).strip()
    return int(s) if re.fullmatch(r"\d{1,4}", s) else None


def _text(v):
    return "" if v is None else str(v).strip()


def parse_response(js, n_codes):
    """Bentuk tetap: header, 6 baris SKU, 3 blok x 6 baris x (1+n_codes) angka. Kekurangan diisi None."""
    n = 1 + n_codes
    h = js.get("header") or {}
    skus = []
    for i in range(SKU_ROWS):
        r = (js.get("skus") or [])[i] if i < len(js.get("skus") or []) else []
        r = list(r or []) + [None] * 3
        skus.append({"tipe": _text(r[0]), "warna": _text(r[1]), "size": _text(r[2])})
    blocks = []
    for b in range(WINDOWS):
        blk = (js.get("blocks") or [])[b] if b < len(js.get("blocks") or []) else {}
        rows_raw = (blk or {}).get("rows") or []
        rows = []
        for i in range(SKU_ROWS):
            r = list(rows_raw[i]) if i < len(rows_raw) and rows_raw[i] else []
            r = (r + [None] * n)[:n]
            rows.append([_int(v) for v in r])
        blocks.append({"checked": bool((blk or {}).get("checked")), "rows": rows})
    shift = _text(h.get("shift")).lower()
    return {"header": {"mesin": _text(h.get("mesin")), "tanggal": _text(h.get("tanggal")),
                       "shift": shift if shift in CHECKPOINTS else "", "tim": _text(h.get("tim")).upper()[:1]},
            "skus": skus, "blocks": blocks}


def block_filled(block):
    """Blok dianggap sudah diperiksa kalau kotak Diperiksa dicentang atau ada angka > 0."""
    return block["checked"] or any(v for row in block["rows"] for v in row if v)


def active_rows(parsed):
    """Indeks baris S yang dipakai: tulisan SKU ada, atau ada angka > 0 di blok manapun."""
    out = []
    for i in range(SKU_ROWS):
        s = parsed["skus"][i]
        has_sku = any(s.values())
        has_num = any(v for b in parsed["blocks"] for v in b["rows"][i] if v)
        if has_sku or has_num:
            out.append(i)
    return out


# ----------------------------------------------------------------------
# Header: mesin dan tanggal
# ----------------------------------------------------------------------
def match_machine(text, machine_codes):
    """'M-06', 'M06', '6' -> kode mesin dengan angka sama. None kalau tidak ada yang cocok."""
    d = re.sub(r"\D", "", text or "")
    if not d:
        return None
    hits = [c for c in machine_codes if re.sub(r"\D", "", c) and int(re.sub(r"\D", "", c)) == int(d)]
    return hits[0] if len(hits) == 1 else None


def parse_date(text):
    s = (text or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


# ----------------------------------------------------------------------
# Mencocokkan tulisan SKU ke SKU sistem
# ----------------------------------------------------------------------
def norm_type(s):
    d = re.sub(r"\D", "", (s or "").upper().replace("O", "0"))
    return d[-4:] if len(d) >= 4 else d


def norm_color(s):
    return re.sub(r"[^0-9A-Z]", "", (s or "").upper()).translate(str.maketrans("OIL", "011"))


def norm_size(s):
    nums = [int(x) for x in re.findall(r"\d+", s or "")]
    return tuple(nums[:2])


def _dist1(a, b):
    """True kalau a dan b beda paling banyak satu karakter (ganti/sisip/hapus)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    a, b = (a, b) if len(a) < len(b) else (b, a)
    return any(b[:i] + b[i + 1:] == a for i in range(len(b)))


def _score(row, cand):
    """Maks 9: tipe 3 + warna 3 + size 3. Kecocokan dengan beda satu karakter dihargai lebih rendah."""
    t = norm_type(row["tipe"])
    ct = re.sub(r"\D", "", cand["model_code"])
    sc = 3 if t and t == ct else (1 if t and len(t) >= 3 and _dist1(t, ct) else 0)
    c = norm_color(row["warna"])
    cc = norm_color(cand["color_code"])
    sc += 3 if c and c == cc else (1 if c and len(c) >= 3 and _dist1(c, cc) else 0)
    s, cs = norm_size(row["size"]), norm_size(cand["size_label"])
    sc += 3 if s and s == cs else (1 if s and cs and s[0] == cs[0] else 0)
    return sc


def match_row(row, running, all_skus):
    """
    row: {"tipe","warna","size"} tulisan QC. running / all_skus: list dict {"sku_id","model_code","color_code","size_label"}.
    Semua SKU dinilai bersama; SKU yang sedang jalan hanya menang kalau skornya seri (bonus 0,5), supaya tulisan
    yang cocok sempurna dengan SKU lain tidak dipaksa ke SKU jalan yang cuma mirip.
    Return (sku_id | None, status) dengan status:
      blank (baris kosong) | exact (9/9, SKU jalan) | fuzzy (>= 6, SKU jalan) |
      other (cocok tapi BUKAN SKU yang sedang jalan) | none (tidak ada yang cocok jelas; QC memilih manual)
    """
    if not any(row.values()):
        return None, "blank"
    run_ids = {c["sku_id"] for c in running}
    pool = {c["sku_id"]: c for c in all_skus}
    pool.update({c["sku_id"]: c for c in running})
    scored = sorted(((_score(row, c) + (0.5 if sid in run_ids else 0), _score(row, c), sid)
                     for sid, c in pool.items()), key=lambda x: -x[0])
    if not scored or scored[0][1] < 6 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None, "none"
    _, sc, sid = scored[0]
    if sid in run_ids:
        return sid, "exact" if sc >= 9 else "fuzzy"
    return sid, "other"


# ----------------------------------------------------------------------
# Membentuk laporan untuk database
# ----------------------------------------------------------------------
def build_reports(parsed, codes, mapping, selected, shift, saved_checkpoints=()):
    """
    mapping : {indeks baris S (0-5): sku_id | None}
    selected: indeks blok (0-2) yang akan disimpan
    Return (reports, errors); reports = [{"checkpoint", "lines": [...]}] siap untuk submit_bs_report.
    """
    reports, errors = [], []
    ids = [sid for sid in mapping.values() if sid is not None]
    for sid in {s for s in ids if ids.count(s) > 1}:
        rows = ", ".join(f"S{i + 1}" for i, s in mapping.items() if s == sid)
        errors.append(f"Baris {rows} dipetakan ke SKU yang sama; gabungkan jadi satu baris atau pilih SKU yang benar")
    for b in selected:
        cp = CHECKPOINTS[shift][b]
        if cp in saved_checkpoints:
            continue
        lines = []
        for i in range(SKU_ROWS):
            row = parsed["blocks"][b]["rows"][i]
            sid = mapping.get(i)
            if sid is None:
                if any(v for v in row if v):
                    errors.append(f"Pemeriksaan {b + 1}, baris S{i + 1}: ada angka tapi SKU belum dipilih")
                continue
            if any(v is None for v in row):
                errors.append(f"Pemeriksaan {b + 1}, baris S{i + 1}: ada sel yang belum terbaca, isi angkanya (0 kalau kosong)")
                continue
            lines.append({"sku_id": int(sid), "ts_qty": int(row[0]),
                          "defects": [{"bs_code": c["code"], "qty": int(v)} for c, v in zip(codes, row[1:]) if v > 0]})
        if not lines:
            errors.append(f"Pemeriksaan {b + 1}: tidak ada baris SKU yang bisa disimpan")
        reports.append({"checkpoint": cp, "lines": lines})
    return reports, errors
