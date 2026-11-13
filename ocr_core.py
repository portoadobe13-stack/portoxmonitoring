"""
ocr_core.py - logika murni (tanpa Streamlit) untuk scan layar HMI mesin EVA.

Nama parameter (key) HARUS sama dengan tabel param_defs di 02_seed.sql.
"""
import base64
import io
import json
import math
import re
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from PIL import Image, ImageOps

DOORS = list(range(1, 7))            # mesin ini punya 6 stasiun/pintu
INJ_LABEL = {1: "Injector I", 2: "Injector II"}
# Model vision Groq berganti cepat (Llama 4 Scout/Maverick sudah ditutup 2026).
# Bisa ditimpa lewat secrets: GROQ_VISION_MODEL
DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"          # Groq
DEFAULT_CLAUDE_FAST = "claude-haiku-5-5"            # murah & cepat
DEFAULT_CLAUDE_STRONG = "claude-sonnet-5-5"         # lebih teliti
# Harga USD per 1 juta token (input, output) - PERKIRAAN dari situs pelacak harga, cek claude.com/pricing.
PRICES_USD_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}


@dataclass(frozen=True)
class Param:
    key: str
    page: str
    scope: str            # door | injector | machine
    label: str
    unit: Optional[str]
    vtype: str            # number | onoff
    vmin: Optional[float]
    vmax: Optional[float]


# key, page, scope, label, unit, vtype, min, max   (urutan = urutan kolom di layar)
_SPEC = [
    # --- Clamping ---
    ("clamp_pressure", "clamping", "door", "Clamping pressure", "kg/cm2", "number", 0, 130),
    ("clamp_flow", "clamping", "door", "Clamping flow", "%", "number", 0, 70),
    ("clamp_time", "clamping", "door", "Clamping time", "s", "number", None, None),
    ("quick_open_limit", "clamping", "door", "Quick opening time limit", "s", "number", None, None),
    ("supp_pressure", "clamping", "door", "Supplementary pressure", None, "onoff", None, None),
    ("pressure_build_time", "clamping", "machine", "Pressure build time", "s", "number", None, None),
    ("pressure_relief_time", "clamping", "machine", "Pressure relief time", "s", "number", None, None),
    # --- Mould opening & clamping pressure/flow ---
    ("clamp_fast_p", "mould_flow", "door", "Clamping Fast P", "kg/cm2", "number", 0, 150),
    ("clamp_fast_f", "mould_flow", "door", "Clamping Fast F%", "%", "number", 0, 100),
    ("clamp_slow_p", "mould_flow", "door", "Clamping Slow P", "kg/cm2", "number", 0, 150),
    ("clamp_slow_f", "mould_flow", "door", "Clamping Slow F%", "%", "number", 0, 100),
    ("open_fast_p", "mould_flow", "door", "Opening Fast P", "kg/cm2", "number", 0, 150),
    ("open_fast_f", "mould_flow", "door", "Opening Fast F%", "%", "number", 0, 100),
    ("open_slow_p", "mould_flow", "door", "Opening Slow P", "kg/cm2", "number", 0, 150),
    ("open_slow_f", "mould_flow", "door", "Opening Slow F%", "%", "number", 0, 100),
    ("energy_storage_pressure", "mould_flow", "machine", "Energy storage pressure", "kg/cm2", "number", 0, 150),
    ("energy_storage_flow", "mould_flow", "machine", "Energy storage flow", "%", "number", 0, 100),
    ("mould_adjust_pressure", "mould_flow", "machine", "Mould adjustment pressure", "kg/cm2", "number", 0, 150),
    ("mould_adjust_flow", "mould_flow", "machine", "Mould adjustment flow", "%", "number", 0, 100),
    # --- Injector ---
    ("feeding_pressure", "injector", "injector", "Feeding pressure", "kg/cm2", "number", 0, 150),
    ("feeding_flow", "injector", "injector", "Feeding flow", "%", "number", 0, 100),
    ("cleanup_pressure", "injector", "injector", "Clear up material pressure", "kg/cm2", "number", 0, 150),
    ("cleanup_flow", "injector", "injector", "Clear up material flow", "%", "number", 0, 100),
    ("compression_pressure", "injector", "injector", "Compression pressure", "kg/cm2", "number", 0, 150),
    ("compression_flow", "injector", "injector", "Compression flow", "%", "number", 0, 100),
    ("fwd_block_pressure", "injector", "injector", "Injector forward and blocking pressure", "kg/cm2", "number", 0, 150),
    ("back_pressure", "injector", "injector", "Injector backward pressure", "kg/cm2", "number", 0, 150),
    ("fwd_back_nozzle_flow", "injector", "injector", "Injector fwd/bwd and blocking nozzle flow", "%", "number", 0, 100),
    ("screw_speed", "injector", "injector", "Screw speed", "rpm", "number", None, None),
    ("material_compression_time", "injector", "injector", "Material compression time", "s", "number", None, None),
    ("injection_limit_time", "injector", "injector", "Injection limit time", "s", "number", None, None),
    ("prepare_back_time", "injector", "injector", "Prepare injector backward time", "s", "number", None, None),
    ("prepare_move_inj_time", "injector", "machine", "Prepare move injector time", "s", "number", None, None),
    # --- Suhu barrel (SV = angka hijau) ---
    ("barrel_z1", "barrel", "injector", "Barrel zona 1 (SV)", "C", "number", None, None),
    ("barrel_z2", "barrel", "injector", "Barrel zona 2 (SV)", "C", "number", None, None),
    ("barrel_z3", "barrel", "injector", "Barrel zona 3 (SV)", "C", "number", None, None),
    ("barrel_z4", "barrel", "injector", "Barrel zona 4 (SV)", "C", "number", None, None),
    # --- Vulcanization timer ---
    ("vulc_time", "vulcanization", "door", "Vulcanization time", "s", "number", None, None),
    ("block_off", "vulcanization", "door", "Block material off", "s", "number", None, None),
    ("block_on", "vulcanization", "door", "Block material on", "s", "number", None, None),
    ("vacuum_left", "vulcanization", "door", "Vacuum left", "s", "number", None, None),
    ("vacuum_right", "vulcanization", "door", "Vacuum right", "s", "number", None, None),
]

PARAMS = {s[0]: Param(*s) for s in _SPEC}

# Urutan slot di aplikasi + petunjuk letak kolom di layar (dipakai di prompt)
PAGE_META = {
    "clamping": {
        "title": "Setting Clamping",
        "screen": "Clamping parameters & supplementary pressure selection",
        "hint": (
            "Tabel punya 6 baris (Station 1-6). Kolom dari kiri ke kanan: Clamping pressure (kg/cm2), "
            "Clamping flow (%), Clamping time (S), Quick opening time limit (S), supplementary pressure "
            "selection (tombol ON/OFF). Di sisi kanan ada dua kotak: 'Pressure build time' dan "
            "'Pressure relief time' (detik)."
        ),
    },
    "mould_flow": {
        "title": "Mould Opening & Clamping Pressure/Flow",
        "screen": "Mould opening and clamping pressure & flow",
        "hint": (
            "Tabel punya 6 baris (station 1-6) dan 8 kolom angka. Grup 'Clamping': Fast P, Fast F%, Slow P, "
            "Slow F%. Grup 'Opening': Fast P, Fast F%, Slow P, Slow F%. Di bawah tabel ada 4 kotak: "
            "'Energy storage pressure', 'Energy storage flow', 'Mould adjustment pressure', "
            "'Mould adjustment flow'."
        ),
    },
    "injector": {
        "title": "Parameter Injector",
        "screen": "Common parameters of the injector",
        "hint": (
            "Layar punya dua tabel. KIRI 'Common parameters of the injector' dengan kolom Injector I dan "
            "Injector II, baris dari atas ke bawah: Feeding pressure, Feeding flow, Clear up material pre(ssure), "
            "Clear up material flow, Compression pressure, Compression flow, Injector forward and blocking "
            "pressure, Injector backward pressure, Injector forward/backward and blocking nozzle flow, Screw speed. "
            "KANAN 'timing of injector' dengan kolom Injector I dan II: Material compression time, "
            "Injection limit time, Prepare injector backward time; dan satu kotak tunggal "
            "'Prepare move injector time'."
        ),
    },
    "barrel": {
        "title": "Suhu Barrel",
        "screen": "panel pengendali suhu barrel (8 display temperatur)",
        "hint": (
            "Foto panel pengendali suhu. Baris ATAS = Injector I (4 display, zona 1段 2段 3段 4段 dari kiri ke "
            "kanan). Baris BAWAH = Injector II (4 display, zona 1-4 dari kiri ke kanan). Tiap display punya "
            "dua angka: angka MERAH di atas = PV (suhu aktual, JANGAN dipakai) dan angka HIJAU di bawah = SV "
            "(suhu setting, INI yang diambil)."
        ),
    },
    "vulcanization": {
        "title": "Vulcanization Timer",
        "screen": "Vulcanization timer",
        "hint": (
            "Tabel punya 6 baris; baris dari atas ke bawah = station 1-6 (kolom nomor station mungkin "
            "terpotong di foto, tetap urutkan dari atas). Kolom: vulcanization (waktu SETTING), Countdown "
            "(hitung mundur berjalan, ABAIKAN), Block material off, Block material on, Vacuum left, "
            "Vacuum right. Semua dalam detik."
        ),
    },
}
PAGE_ORDER = ["clamping", "mould_flow", "injector", "barrel", "vulcanization"]


def keys_of(page, scope):
    return [p.key for p in PARAMS.values() if p.page == page and p.scope == scope]


# ----------------------------------------------------------------------
# Gambar
# ----------------------------------------------------------------------
def prepare_image(file_like, max_side=1600):
    """Buka foto, putar sesuai EXIF, ubah ke RGB, kecilkan (batas Groq 4 MB base64)."""
    img = Image.open(file_like)
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    return img


def image_to_b64(img, quality=90):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def image_to_data_url(img, quality=90):
    return "data:image/jpeg;base64," + image_to_b64(img, quality)


# ----------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------
def build_prompt(page):
    meta = PAGE_META[page]
    dk, ik, mk = keys_of(page, "door"), keys_of(page, "injector"), keys_of(page, "machine")

    def describe(keys):
        out = []
        for k in keys:
            p = PARAMS[k]
            extra = "ON atau OFF" if p.vtype == "onoff" else (f"angka, {p.unit}" if p.unit else "angka")
            out.append(f"  {k}: {p.label} ({extra})")
        return "\n".join(out)

    parts = [
        f"Foto ini seharusnya layar/panel mesin injeksi EVA: \"{meta['screen']}\".",
        meta["hint"],
        "",
        "Baca SEMUA angka persis seperti tertulis di layar. Angka berawalan nol berarti angka biasa "
        "(\"070\" -> 70, \"07.00\" -> 7.0, \"000\" -> 0). Jangan menebak: jika sebuah angka tidak terbaca "
        "jelas, isi null. Tetap isi null supaya urutan kolom tidak bergeser.",
        "",
    ]
    example = {"status": "success"}
    if dk:
        parts += ["\"doors\": satu baris array untuk tiap station 1-6. Tiap baris berurutan: "
                  "[door_no, " + ", ".join(dk) + "]", describe(dk), ""]
        example["doors"] = [[d] + [None] * len(dk) for d in DOORS]
    if ik:
        parts += ["\"injectors\": kunci \"1\" = Injector I, \"2\" = Injector II. Tiap isi array berurutan: "
                  "[" + ", ".join(ik) + "]", describe(ik), ""]
        example["injectors"] = {"1": [None] * len(ik), "2": [None] * len(ik)}
    if mk:
        parts += ["\"machine\": satu array nilai tunggal berurutan: [" + ", ".join(mk) + "]", describe(mk), ""]
        example["machine"] = [None] * len(mk)
    parts += [
        "Jawab HANYA dengan satu objek JSON, ditulis dalam SATU baris tanpa spasi, indentasi, atau komentar. "
        "Bentuk (ganti null dengan nilai yang dibaca):",
        json.dumps(example, ensure_ascii=False, separators=(",", ":")),
        "",
        'Jika foto BUKAN layar tersebut, terlalu buram, atau terpotong sehingga tabel tidak bisa dibaca, jawab: '
        '{"status":"error","message":"alasan singkat"}',
    ]
    return "\n".join(parts)


# ----------------------------------------------------------------------
# Parsing jawaban model
# ----------------------------------------------------------------------
def extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Jawaban model bukan JSON")
    return json.loads(m.group(0))


def clean_value(key, raw):
    """Ubah nilai mentah dari model jadi float / 'ON' / 'OFF' / None."""
    p = PARAMS[key]
    if raw is None:
        return None
    if p.vtype == "onoff":
        s = str(raw).strip().upper()
        return s if s in ("ON", "OFF") else None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return None if (isinstance(raw, float) and math.isnan(raw)) else float(raw)
    s = str(raw).strip().replace(",", ".")
    m = re.fullmatch(r"-?\d+(\.\d+)?", s)
    return float(s) if m else None


def parse_response(page, js):
    """JSON model -> {(key, idx): nilai}. Menerima baris array (format kompak) maupun objek berlabel.
    Hanya key milik halaman ini yang diterima."""
    values = {}
    dk, ik, mk = keys_of(page, "door"), keys_of(page, "injector"), keys_of(page, "machine")

    doors = js.get("doors") or []
    if isinstance(doors, dict):          # {"1": [...]} atau {"1": {...}}
        doors = [([k] + list(v)) if isinstance(v, (list, tuple)) else {"door_no": k, **v}
                 for k, v in doors.items()]
    for pos, row in enumerate(doors, start=1):
        if isinstance(row, dict):
            d, cells = row.get("door_no"), {k: row.get(k) for k in dk}
        elif isinstance(row, (list, tuple)):
            if len(row) == len(dk) + 1:
                d, arr = row[0], row[1:]
            else:                        # tanpa door_no -> pakai urutan baris
                d, arr = pos, row
            cells = dict(zip(dk, arr))
        else:
            continue
        try:
            d = int(d)
        except (TypeError, ValueError):
            continue
        if d in DOORS:
            for k in dk:
                values[(k, d)] = clean_value(k, cells.get(k))

    inj = js.get("injectors") or {}
    if isinstance(inj, (list, tuple)):
        inj = {str(i + 1): r for i, r in enumerate(inj)}
    for i in (1, 2):
        row = inj.get(str(i)) or inj.get(i) or {}
        if isinstance(row, (list, tuple)):
            row = dict(zip(ik, row))
        for k in ik:
            values[(k, i)] = clean_value(k, row.get(k))

    mach = js.get("machine") or {}
    if isinstance(mach, (list, tuple)):
        mach = dict(zip(mk, mach))
    for k in mk:
        values[(k, 0)] = clean_value(k, mach.get(k))
    return values


def expected_cells(page):
    n = len(keys_of(page, "door")) * len(DOORS) + len(keys_of(page, "injector")) * 2 \
        + len(keys_of(page, "machine"))
    return n


def scan_with_groq(client, model, page, img):
    """Return (values, error_message, usage). Tidak melempar exception."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": build_prompt(page)},
            {"type": "image_url", "image_url": {"url": image_to_data_url(img)}},
        ],
    }]
    base = dict(model=model, messages=messages, temperature=0, max_completion_tokens=600)
    # Urutan percobaan: mode non-thinking + JSON mode -> tanpa JSON mode -> tanpa parameter reasoning
    attempts = [
        dict(response_format={"type": "json_object"}, extra_body={"reasoning_effort": "none"}),
        dict(extra_body={"reasoning_effort": "none"}),
        dict(response_format={"type": "json_object"}),
        dict(),
    ]
    js, last_err, comp = None, None, None
    for extra in attempts:
        try:
            comp = client.chat.completions.create(**base, **extra)
            js = extract_json(comp.choices[0].message.content)
            break
        except Exception as e:
            last_err = e
            if any(t in str(e).lower() for t in ("model_not_found", "decommission", "429", "rate_limit")):
                break                      # ganti model / tunggu limit, bukan masalah parameter
    u = getattr(comp, "usage", None)
    usage = ({"in": getattr(u, "prompt_tokens", None), "out": getattr(u, "completion_tokens", None)}
             if u is not None else None)
    if js is None:
        msg = f"Gagal memanggil/menafsirkan jawaban Groq: {last_err}"
        if "Request too large" in str(last_err):
            return None, ("Permintaan melebihi batas token Groq untuk akun ini. "
                          f"Detail: {last_err}"), None
        if "429" in str(last_err) or "rate_limit" in str(last_err).lower():
            return None, ("Batas pemakaian Groq tercapai (per menit atau per hari). Tunggu 1 menit lalu scan lagi; "
                          "kalau tetap gagal, kuota harian free tier kemungkinan habis (reset tiap hari). "
                          f"Detail: {last_err}"), None
        if "model_not_found" in str(last_err):
            msg += (f"\n\nModel '{model}' tidak tersedia untuk akun ini. Isi GROQ_VISION_MODEL di "
                    "secrets dengan model vision yang aktif (lihat console.groq.com/docs/vision).")
        return None, msg, None
    if js.get("status") != "success":
        return None, js.get("message", "Foto tidak valid"), usage
    return parse_response(page, js), None, usage


def _claude_error(e, model):
    status = getattr(e, "status_code", None)
    text = str(e)
    low = text.lower()
    if "credit balance" in low:
        return "Saldo kredit Claude habis. Isi ulang di Claude Console → Billing."
    if status == 401 or "authentication" in low or "invalid x-api-key" in low:
        return "ANTHROPIC_API_KEY salah atau tidak aktif. Periksa di secrets dan di Claude Console."
    if status == 404 or "not_found" in low:
        return (f"Model '{model}' tidak tersedia untuk akun ini. Ganti lewat secrets "
                "(CLAUDE_FAST_MODEL / CLAUDE_STRONG_MODEL) dengan model yang aktif.")
    if status == 429:
        return f"Batas pemakaian Claude tercapai. Tunggu sebentar lalu scan lagi. Detail: {text}"
    if status in (500, 502, 503, 529) or "overloaded" in low:
        return "Server Claude sedang sibuk. Coba scan lagi beberapa detik lagi."
    return f"Gagal memanggil/menafsirkan jawaban Claude: {text}"


def scan_with_claude(client, model, page, img):
    """Return (values, error_message, usage). Gambar ditaruh SEBELUM teks (anjuran Anthropic)."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": image_to_b64(img)}},
            {"type": "text", "text": build_prompt(page)},
        ],
    }]
    try:
        # temperature sengaja tidak dikirim: sebagian model Claude terbaru menolak parameter sampling
        resp = client.messages.create(model=model, max_tokens=600, messages=messages)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        js = extract_json(text)
        usage = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
    except Exception as e:
        return None, _claude_error(e, model), None
    if js.get("status") != "success":
        return None, js.get("message", "Foto tidak valid"), usage
    return parse_response(page, js), None, usage


def scan_image(provider, client, model, page, img):
    """provider: 'claude' | 'groq'. Return (values, error, usage)."""
    if provider == "claude":
        return scan_with_claude(client, model, page, img)
    return scan_with_groq(client, model, page, img)


def estimate_cost(model, usage):
    """USD per scan, atau None kalau harga model tidak diketahui."""
    price = PRICES_USD_PER_MTOK.get(model)
    if not price or not usage or usage.get("in") is None or usage.get("out") is None:
        return None
    return (usage["in"] * price[0] + usage["out"] * price[1]) / 1_000_000


# ----------------------------------------------------------------------
# Tabel editor (tampilan mirip layar HMI) <-> nilai
# ----------------------------------------------------------------------
def _num(series):
    return pd.to_numeric(series, errors="coerce")


def make_frames(page, values):
    frames = {}
    dk = keys_of(page, "door")
    if dk:
        df = pd.DataFrame(index=pd.Index(DOORS, name="Pintu"),
                          columns=[PARAMS[k].label for k in dk], dtype=object)
        for k in dk:
            col = PARAMS[k].label
            for d in DOORS:
                df.at[d, col] = values.get((k, d))
            if PARAMS[k].vtype == "number":
                df[col] = _num(df[col])
        frames["door"] = df
    ik = keys_of(page, "injector")
    if ik:
        df = pd.DataFrame(index=pd.Index([PARAMS[k].label for k in ik], name="Parameter"),
                          columns=list(INJ_LABEL.values()), dtype=object)
        for k in ik:
            for i, name in INJ_LABEL.items():
                df.at[PARAMS[k].label, name] = values.get((k, i))
        frames["injector"] = df.apply(_num)
    mk = keys_of(page, "machine")
    if mk:
        df = pd.DataFrame(index=pd.Index([PARAMS[k].label for k in mk], name="Parameter"),
                          columns=["Nilai"], dtype=object)
        for k in mk:
            df.at[PARAMS[k].label, "Nilai"] = values.get((k, 0))
        frames["machine"] = df.apply(_num)
    return frames


def _py(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        return v
    return float(v)


def frames_to_values(page, frames):
    values = {}
    if "door" in frames:
        df = frames["door"]
        for k in keys_of(page, "door"):
            for d in DOORS:
                values[(k, d)] = _py(df.at[d, PARAMS[k].label])
    if "injector" in frames:
        df = frames["injector"]
        for k in keys_of(page, "injector"):
            for i, name in INJ_LABEL.items():
                values[(k, i)] = _py(df.at[PARAMS[k].label, name])
    if "machine" in frames:
        df = frames["machine"]
        for k in keys_of(page, "machine"):
            values[(k, 0)] = _py(df.at[PARAMS[k].label, "Nilai"])
    return values


def range_warnings(values):
    out = []
    for (k, idx), v in values.items():
        p = PARAMS[k]
        if isinstance(v, float) and ((p.vmin is not None and v < p.vmin) or (p.vmax is not None and v > p.vmax)):
            who = {"door": f"Pintu {idx}", "injector": INJ_LABEL.get(idx, ""), "machine": "Mesin"}[p.scope]
            out.append(f"{who} - {p.label} = {v:g} (rentang layar {p.vmin:g}-{p.vmax:g} {p.unit or ''})")
    return out


def count_filled(values):
    return sum(v is not None for v in values.values())


# ----------------------------------------------------------------------
# Payload untuk submit_setting_report(p_values)
# ----------------------------------------------------------------------
def build_value_rows(verified, ocr):
    """verified & ocr: {(key, idx): nilai}. Sel kosong dilewati."""
    rows = []
    for (k, idx), v in verified.items():
        if v is None:
            continue
        o = (ocr or {}).get((k, idx))
        row = {"param_key": k, "idx": idx, "source": "ocr" if o is not None else "manual"}
        if PARAMS[k].vtype == "onoff":
            row["value_text"] = v
            row["ocr_text"] = o
        else:
            row["value_num"] = v
            row["ocr_num"] = o
        rows.append(row)
    return rows
